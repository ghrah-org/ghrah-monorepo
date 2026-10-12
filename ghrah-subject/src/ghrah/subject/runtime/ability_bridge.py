# SPDX-FileCopyrightText: 2026 chenxya <chenxya@ghrah.org>
#
# SPDX-License-Identifier: Apache-2.0

"""AbilityBridgeUnit：插件能力 → Agent 工具面的装配桥（command_proxy 投影）。

重算语义（全量、幂等、可重入）：

1. 输入（懒解析，reconcile 时现取）：
   - ``PLUGIN_SESSION_SERVICE.state.specs``——已挂载插件的
     ``provides.commands`` / ``provides.capabilities``（场景 A 声明）；
   - ``ABILITY_BRIDGE`` 服务——包装插件提供的 capability → descriptor
     实质映射（场景 B）；
2. 闭环校验：capability 的目标命令不在场（命令插件未挂载/被裁撤）→
   该 capability 不装配并记 warning（对齐 ``verify_plugins`` 的
   capability_missing 形状）；
3. 场景 A 默认 descriptor：配对规则 = capability 的 name 段与
   ``provides.commands`` 同名精确匹配（cap ``foo:run`` ↔ command ``run``），
   工具名 = name 段，参数 schema 缺省为 ``{"payload": object}``；
   场景 B 服务映射优先（同一 capability 有包装时不落默认）；
4. diff 生效：装配范围 = 该 cluster 全部存量 Agent（Q1 裁定：存量全部 +
   后续 spawn 均可见——经 ``agent_spawned`` 事件触发全量重算；该语义为
   **过渡语义**，D2 收敛为 manifest 声明驱动）。REGISTER/UNREGISTER_ABILITY
   为 per-agent 命令，逐 ``(cluster_id, agent_id)`` dispatch。

装配账本 ``_assembled``：``(cluster_id, agent_id) → {tool_name}``——仅是
可从 specs + 服务映射完整重建的投影（SSOT 归插件装配状态与服务面），
diff 的内存底账；agent 终止即剪除（agent 消亡时工具随实例消失，
无需 Core 侧撤销）。

HITL 闸门不在本 unit：Core 物化器强制（``require_hitl`` 默认 True，仅
``auto_approve_abilities`` 白名单可关闭），descriptor 声明不具自批效力。
"""

from __future__ import annotations

import asyncio
import logging
from typing import TYPE_CHECKING, Any

from ghrah.subject.runtime.plugin_session import PLUGIN_SESSION_SERVICE
from ghrah.subject.runtime.service_keys import (
    ABILITY_BRIDGE,
    ABILITY_BRIDGE_UNIT,
    CORE_CLUSTER_REGISTRY,
)
from ghrah.subject.unit.base import RouteSpec, SubjectUnit, UnitMeta

if TYPE_CHECKING:
    from ouroboros import Context  # type: ignore[import-untyped]

__all__ = ["AbilityBridgeUnit"]

logger = logging.getLogger(__name__)

# 场景 A 默认参数 schema：命令处理器无 schema 声明机制时的缺省形状
_DEFAULT_PARAMETERS: dict[str, Any] = {
    "type": "object",
    "properties": {"payload": {"type": "object"}},
}


class AbilityBridgeUnit(SubjectUnit):
    """插件能力装配桥 unit（挂载名 ``ability_bridge``，full profile）。

    重算触发面：plugin_mount 挂载/崩溃路径经 ``ABILITY_BRIDGE_UNIT``
    服务显式通知；``agent_spawned``/``agent_terminated`` 事件订阅。
    """

    def __init__(self) -> None:
        self._meta = UnitMeta(
            name="ability_bridge",
            requires=frozenset({CORE_CLUSTER_REGISTRY}),
            provides=frozenset({ABILITY_BRIDGE_UNIT}),
            routes=RouteSpec(events=frozenset({"agent_spawned", "agent_terminated"})),
        )
        self._ctx: Context | None = None
        self._lock = asyncio.Lock()
        # 装配账本（可重建投影）：(cluster_id, agent_id) → {tool_name}
        self._assembled: dict[tuple[str, str], set[str]] = {}

    @property
    def meta(self) -> UnitMeta:
        return self._meta

    async def init(self, ctx: Context) -> None:
        self._ctx = ctx
        ctx.provide(ABILITY_BRIDGE_UNIT.name, self)

    async def handle_event(self, event_type: str, payload: dict[str, Any]) -> None:
        if event_type == "agent_spawned":
            # 全量幂等重算覆盖新 agent（Q1：后续 spawn 均可见）
            await self.reconcile()
        elif event_type == "agent_terminated":
            agent_id = str(payload.get("agent_id") or "")
            if agent_id:
                self._assembled = {
                    key: tools for key, tools in self._assembled.items() if key[1] != agent_id
                }

    # ----------------------------------------------------------------
    # 重算与生效
    # ----------------------------------------------------------------

    async def reconcile(self) -> None:
        """全量重算装配集，按 cluster × agent diff 后逐 agent 生效。

        registry 不在场（coexistence profile / 未挂载）或 ctx 未初始化时
        静默跳过；单飞（lock）防挂载钩子与事件并发重入。
        """
        if self._ctx is None:
            return
        registry = self._ctx.get(CORE_CLUSTER_REGISTRY.name, strict=False)
        if registry is None:
            logger.debug("ability bridge: no core cluster registry mounted; skip reconcile.")
            return
        desired = self._desired_descriptors()
        if not desired and not self._assembled:
            return
        async with self._lock:
            for cluster_id in registry.cluster_ids:
                try:
                    handle = registry.get_handle(cluster_id)
                except KeyError:
                    continue
                await self._reconcile_cluster(cluster_id, handle, desired)

    def _desired_descriptors(self) -> dict[str, dict[str, Any]]:
        """计算期望装配集：场景 A 声明默认 ∪ 场景 B 服务映射（闭环校验后）。

        Returns:
            ``{tool_name: descriptor dict}``——descriptor dict 字段面与 Core
            侧 ``CommandProxyDescriptor`` 对齐，校验权威在 Core 物化器。
        """
        session = self._ctx.get(PLUGIN_SESSION_SERVICE.name, strict=False)  # type: ignore[union-attr]
        specs: dict[str, Any] = dict(session.state.specs) if session is not None else {}
        bridge = self._ctx.get(ABILITY_BRIDGE.name, strict=False)  # type: ignore[union-attr]
        service_caps: dict[str, dict[str, Any]] = (
            dict(bridge.capabilities()) if bridge is not None else {}
        )

        # 已挂载命令全集（闭环校验的「在场」判据）
        mounted_commands: set[str] = set()
        for spec in specs.values():
            mounted_commands.update(spec.provides.commands)

        desired: dict[str, dict[str, Any]] = {}

        def _merge(capability: str, descriptor: dict[str, Any]) -> None:
            tool_name = str(descriptor.get("tool_name") or "")
            if not tool_name:
                logger.warning(
                    "ability bridge: capability '%s' has no tool_name; skipped.", capability
                )
                return
            if tool_name in desired:
                logger.warning(
                    "ability bridge: tool name '%s' from capability '%s' conflicts with an "
                    "existing mapping; the later one wins.",
                    tool_name,
                    capability,
                )
            desired[tool_name] = descriptor

        # 场景 B：包装插件的服务映射（实质优先于声明默认）
        for capability, descriptor in service_caps.items():
            command = str(descriptor.get("command") or "")
            if command not in mounted_commands:
                logger.warning(
                    "ability bridge: capability '%s' targets command '%s' which is not "
                    "mounted; skipped (capability_missing).",
                    capability,
                    command,
                )
                continue
            _merge(capability, dict(descriptor))

        # 场景 A：插件自声明（服务映射未覆盖的 capability 生成默认 descriptor）
        for spec in specs.values():
            for capability in spec.provides.capabilities:
                if capability in service_caps:
                    continue
                name_seg = capability.split(":", 1)[1]
                if name_seg not in spec.provides.commands:
                    logger.warning(
                        "ability bridge: capability '%s' has no matching command '%s' "
                        "provided by '%s'; skipped (capability_missing).",
                        capability,
                        name_seg,
                        spec.plugin_id,
                    )
                    continue
                _merge(
                    capability,
                    {
                        "tool_name": name_seg,
                        "description": f"Proxy to plugin command '{name_seg}' "
                        f"(provided by '{spec.plugin_id}').",
                        "parameters": _DEFAULT_PARAMETERS,
                        "command": name_seg,
                        "require_hitl": True,
                        "timeout": None,
                    },
                )
        return desired

    async def _reconcile_cluster(
        self,
        cluster_id: str,
        handle: Any,
        desired: dict[str, dict[str, Any]],
    ) -> None:
        """单 cluster 内逐 agent diff 生效，并剪除已消亡 agent 的账本项。"""
        agents = await handle.list_agents()
        live_keys: set[tuple[str, str]] = set()
        for agent in agents:
            agent_id = str(agent.get("agent_id") or "")
            agent_name = str(agent.get("name") or "")
            project_id = str(agent.get("project_id") or "")
            if not agent_id or not agent_name:
                continue
            live_keys.add((cluster_id, agent_id))
            key = (cluster_id, agent_id)
            current = self._assembled.get(key, set())

            for tool_name in sorted(desired.keys() - current):
                descriptor = desired[tool_name]
                result = await handle.dispatch(
                    "register_ability",
                    {
                        "project_id": project_id,
                        "agent_id": agent_id,
                        "agent_name": agent_name,
                        "ability": {
                            "ability_type": "command_proxy",
                            "params": descriptor,
                        },
                    },
                )
                if result.get("success"):
                    self._assembled.setdefault(key, set()).add(tool_name)
                    logger.info(
                        "ability bridge: tool '%s' assembled for agent '%s' (cluster '%s').",
                        tool_name,
                        agent_name,
                        cluster_id,
                    )
                else:
                    logger.warning(
                        "ability bridge: register_ability '%s' for agent '%s' failed: %s",
                        tool_name,
                        agent_name,
                        result.get("error"),
                    )

            for tool_name in sorted(current - desired.keys()):
                result = await handle.dispatch(
                    "unregister_ability",
                    {
                        "project_id": project_id,
                        "agent_id": agent_id,
                        "agent_name": agent_name,
                        "ability_name": tool_name,
                    },
                )
                if result.get("success"):
                    tools = self._assembled.get(key, set())
                    tools.discard(tool_name)
                    if not tools:
                        self._assembled.pop(key, None)
                    logger.info(
                        "ability bridge: tool '%s' disassembled for agent '%s' (cluster '%s').",
                        tool_name,
                        agent_name,
                        cluster_id,
                    )
                else:
                    logger.warning(
                        "ability bridge: unregister_ability '%s' for agent '%s' failed: %s",
                        tool_name,
                        agent_name,
                        result.get("error"),
                    )

        # agent 消亡（终止/崩溃，无事件兜底）：账本剪除，工具随实例消失
        for key in list(self._assembled):
            if key[0] == cluster_id and key not in live_keys:
                del self._assembled[key]
