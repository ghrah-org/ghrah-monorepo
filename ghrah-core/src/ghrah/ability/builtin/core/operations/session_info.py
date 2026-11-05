# SPDX-FileCopyrightText: 2026 chenxya <chenxya@ghrah.org>
#
# SPDX-License-Identifier: Apache-2.0

"""SessionInfoAbility：环境/会话自省原语（008 T-2 工具形态）。

部署形态、授权根、HITL 参数等环境事实对 agent 不可见是 008 盘点确认的
工具设计缺陷：LLM 无法自知"能访问哪些路径/审批等多久/是否挂进集群"，
只能试探撞墙。本能力提供运行时只读自省面：

- 静态部分（构造注入）：spawn 装配期由 CoreUnit._environment_snapshot
  组装的部署快照（deployment/workspace_root/allowed_roots/hitl 参数）；
- 动态部分（execute 叠加）：agent_name、session 信息（``CONTEXT_MANAGER``
  服务接线时）、集群成员（``SUPERVISOR`` 服务接线时）。

静态快照缺失（standalone 裸用、非 CoreUnit 装配）时返回明确指路而非
猜测；动态部分按各自接线状态叠加，任一缺失只缺对应段，不整体失败。
"""

from __future__ import annotations

from typing import TYPE_CHECKING, Any

from pydantic import BaseModel, ConfigDict

from ghrah.ability.execution.base import Ability
from ghrah.ability.execution.execution_services import CONTEXT_MANAGER, SUPERVISOR
from ghrah.types.results import ActionOutcome, ActionResult

if TYPE_CHECKING:
    from ghrah.ability.execution.execution_services import ExecutionServices
    from ghrah.ability.execution.hooks import Hook
    from ghrah.ability.execution.invocation import OperationCall


# ---------------------------------------------------------------------------
# Operation 声明（v1）：build 从下列常量与 Input/Output 模型生成 ability.yaml 的 members。
# 旧 manifest 的 metadata.description / tags / permissions 分别落在 SUMMARY / TAGS / PERMISSIONS。
# ---------------------------------------------------------------------------
PERMISSIONS = {}

# --- 成员声明（build 生成 members 的唯一来源；改完须重新 build）
TITLE = "Session Info"
SUMMARY = (
    "Read-only introspection of the runtime environment (deployment mode, allowed roots, "
    "HITL parameters), current session, and cluster members."
)
TAGS = [
    "core",
    "introspection",
]
EFFECTS = []
TIMEOUT_MS = 60_000
# --- 成员声明结束

__all__ = ["SessionInfoAbility"]


class Input(BaseModel):
    """session_info 入参契约：无参数（额外字段拒绝）。"""

    model_config = ConfigDict(extra="forbid")


class SessionInfoAbility(Ability):
    """环境/会话自省——静态部署快照 + 动态运行信息。

    静态快照经构造注入（spawn 装配期组装），execute 只读叠加：
    - ``environment``：构造注入的部署快照（可能为 None）；
    - ``agent``：agent_name + session 信息（``CONTEXT_MANAGER`` 服务接线时）；
    - ``cluster``：集群成员（``SUPERVISOR`` 服务接线时）。

    用法::

        # CoreUnit spawn 装配（经扫盘工厂或 materialize）
        ability = SessionInfoAbility(environment_info=snapshot)

        # standalone 裸用（无快照，自省面降级为动态部分）
        ability = SessionInfoAbility()
    """

    def __init__(
        self,
        environment_info: dict[str, Any] | None = None,
        hooks: list[Hook] | None = None,
    ) -> None:
        self._environment = dict(environment_info) if environment_info else None
        self._hooks = hooks or []

    def set_environment_info(self, environment_info: dict[str, Any]) -> None:
        """回填静态环境快照（spawn 装配期由 CoreUnit 注入）。

        Args:
            environment_info: 部署快照（deployment/workspace_root/
                allowed_roots/hitl 参数等）。
        """
        self._environment = dict(environment_info)

    @property
    def name(self) -> str:
        return "session_info"

    def bind_tool(self) -> dict[str, Any]:
        return {
            "type": "function",
            "function": {
                "name": "session_info",
                "description": (
                    "Introspect the runtime environment and session: "
                    "deployment mode, workspace root, allowed filesystem "
                    "roots, HITL approval parameters, current session, and "
                    "cluster members. Read-only, no side effects."
                ),
                "parameters": {"type": "object", "properties": {}, "required": []},
            },
        }

    def to_prompt_description(self) -> str:
        return (
            "session_info() -> dict: Read-only introspection of the runtime "
            "environment (deployment, allowed roots, HITL parameters), "
            "current session, and cluster members"
        )

    def get_hooks(self) -> list[Hook]:
        return list(self._hooks)

    async def execute(self, call: OperationCall, services: ExecutionServices) -> ActionResult:
        """组装静态快照 + 动态运行信息。

        Args:
            call: 本次调用的值对象。
            services: 执行期服务表。

        Returns:
            ActionResult：SUCCESS 时 data 含 environment/agent/cluster 三段
            （未接线的段为 None 或省略，不整体失败）。
        """
        data: dict[str, Any] = {
            "environment": self._environment,
            "environment_source": (
                "spawn-time snapshot"
                if self._environment is not None
                else "unavailable (standalone assembly or snapshot not wired) — "
                "deployment parameters are unknown to this agent"
            ),
        }

        # ---- agent/session 段（context_manager 接线时叠加） ----
        agent: dict[str, Any] = {"name": call.identity.agent_name}
        cm = services.get(CONTEXT_MANAGER)
        if cm is not None and hasattr(cm, "get_active_session"):
            try:
                session = cm.get_active_session()
                if session is not None:
                    agent["session_id"] = getattr(session, "session_id", None)
                    agent["active_branch_id"] = getattr(session, "active_branch_id", None)
            except Exception as e:
                agent["session_error"] = f"failed to read active session: {e}"
        else:
            agent["session"] = "context_manager not wired (session info unavailable)"
        data["agent"] = agent

        # ---- cluster 段（supervisor 接线时叠加） ----
        supervisor = services.get(SUPERVISOR)
        if supervisor is not None and hasattr(supervisor, "list_agents"):
            try:
                agents = await supervisor.list_agents()
                data["cluster"] = {
                    "members": agents,
                    "count": len(agents),
                }
            except Exception as e:
                data["cluster"] = {"error": f"failed to list cluster members: {e}"}
        else:
            data["cluster"] = None

        return ActionResult(outcome=ActionOutcome.SUCCESS, data=data)
