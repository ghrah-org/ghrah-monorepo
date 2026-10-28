# SPDX-FileCopyrightText: 2026 chenxya <chenxya@ghrah.org>
#
# SPDX-License-Identifier: Apache-2.0

"""SessionInfoAbility：环境/会话自省原语（008 T-2 工具形态）。

部署形态、授权根、HITL 参数等环境事实对 agent 不可见是 008 盘点确认的
工具设计缺陷：LLM 无法自知"能访问哪些路径/审批等多久/是否挂进集群"，
只能试探撞墙。本能力提供运行时只读自省面：

- 静态部分（构造注入）：spawn 装配期由 CoreUnit._environment_snapshot
  组装的部署快照（deployment/workspace_root/allowed_roots/hitl 参数）；
- 动态部分（execute 叠加）：agent_name、session 信息（context_manager
  接线时）、集群成员（supervisor 接线时）。

静态快照缺失（standalone 裸用、非 CoreUnit 装配）时返回明确指路而非
猜测；动态部分按各自接线状态叠加，任一缺失只缺对应段，不整体失败。
"""

from __future__ import annotations

from typing import TYPE_CHECKING, Any

from ghrah.scheme.execution.base import Ability
from ghrah.types.results import ActionOutcome, ActionResult

if TYPE_CHECKING:
    from ghrah.scheme.execution.context import AbilityExecutionContext
    from ghrah.scheme.execution.hooks import Hook


# ---------------------------------------------------------------------------
# scheme 运行时声明（随实现搬迁；定义文件 ability.yaml 不承载这些事实）
#   来源：ghrah/abilities/builtin/session_info.py
#   PERMISSIONS <- 旧 manifest 的 metadata.permissions
#   HOOKS       <- 旧 manifest 的 hooks
# 切换时由 scheme 加载器合并回旧 manifest 形态；在此之前这两项不是权威。
# ---------------------------------------------------------------------------
PERMISSIONS = {}
HOOKS = {}

# --- catalogue 声明（旧 manifest 的元信息，草案格式里暂无字段承载）
#   SUMMARY <- metadata.description（目录摘要）
#   TAGS    <- metadata.tags（发现/过滤用）
# 草案补上对应字段后，这两项应迁回 ability.yaml。
SUMMARY = (
    "Read-only introspection of the runtime environment (deployment mode, allowed roots, "
    "HITL parameters), current session, and cluster members."
)
TAGS = [
    "core",
    "introspection",
]
# --- catalogue 声明结束

__all__ = ["SessionInfoAbility"]


class SessionInfoAbility(Ability):
    """环境/会话自省——静态部署快照 + 动态运行信息。

    静态快照经构造注入（spawn 装配期组装），execute 只读叠加：
    - ``environment``：构造注入的部署快照（可能为 None）；
    - ``agent``：agent_name + session 信息（context_manager 接线时）；
    - ``cluster``：集群成员（supervisor 接线时）。

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

    async def execute(self, context: AbilityExecutionContext) -> ActionResult:
        """组装静态快照 + 动态运行信息。

        Args:
            context: 执行上下文

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
        agent: dict[str, Any] = {"name": context.agent_name}
        cm = context.context_manager
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
        supervisor = context.supervisor
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
