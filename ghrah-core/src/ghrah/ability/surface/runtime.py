# SPDX-License-Identifier: Apache-2.0
"""单个受信 Agent/run 的工具面：定义装配、按需加载和执行路由。"""

from __future__ import annotations

import json
import logging
from collections import OrderedDict
from dataclasses import asdict, replace
from typing import Any

from pydantic import BaseModel, ConfigDict, Field

from ghrah.ability.access.errors import AccessError
from ghrah.ability.access.store import AccessIdentity
from ghrah.ability.execution.base import Ability
from ghrah.ability.execution.invocation import OperationCall
from ghrah.ability.scripts.runner import execute_run_script
from ghrah.ability.surface.assembly import SurfaceThresholds, assemble_surface
from ghrah.ability.surface.catalog import SurfaceTool, read_agent_catalog
from ghrah.ability.surface.config import DEFAULT_LOADED_LIMIT, DEFAULT_SEARCH_LIMIT, SurfaceConfig
from ghrah.ability.surface.naming import META_TOOL_NAMES, RESERVED_TOOL_NAMES
from ghrah.ability.surface.search import AgentToolSearch
from ghrah.context.token_estimator import effective_length
from ghrah.types.results import ActionOutcome, ActionResult

logger = logging.getLogger(__name__)
DISCLOSURE_GUIDANCE = (
    "When the visible tools are insufficient, use search_tools to discover granted tools, "
    "then describe_tool to inspect their schemas. Described tools may be called directly on "
    "the next turn; execute_tool is also available. Discovery never grants new permissions."
)


def _failure(message: str) -> ActionResult:
    return ActionResult(ActionOutcome.FAILURE, {"error": message})


def _cost(definitions: list[dict[str, Any]]) -> int:
    return sum(
        max(1, effective_length(json.dumps(item, ensure_ascii=False, sort_keys=True)))
        for item in definitions
    )


class ToolSurfaceRuntime:
    """从关系库单向重建可见面；加载状态仅在本次运行有效。"""

    def __init__(
        self,
        *,
        store: Any,
        source: Any,
        agent: str,
        table: Any,
        config: SurfaceConfig | None = None,
        thresholds: SurfaceThresholds | None = None,
        search_limit: int = DEFAULT_SEARCH_LIMIT,
        loaded_limit: int = DEFAULT_LOADED_LIMIT,
    ) -> None:
        if type(loaded_limit) is not int or loaded_limit < 0:
            raise ValueError("loaded_limit 必须是非负整数")
        self.store, self.source, self.agent, self.table = store, source, agent, table
        self.config = config.model_copy(deep=True) if config is not None else SurfaceConfig()
        self.thresholds = thresholds or SurfaceThresholds()
        self.loaded_limit, self.search_limit = loaded_limit, search_limit
        self.run = ""
        self.closed = False
        self.progressive = False
        self.executor: Any = None
        self._loaded: dict[str, tuple] = {}
        self._static_names: set[str] = set()
        self._lru: OrderedDict[str, None] = OrderedDict()
        self._audit: list[dict[str, Any]] = []
        self._search: AgentToolSearch | None = None

    def bind_run(self, run: str) -> None:
        """由生命周期边界固定 run，不允许旧对象绑定新代次。"""
        if not run or self.closed or (self.run and self.run != run):
            raise ValueError("工具面不能绑定另一运行代次")
        self.run = run
        self._search = AgentToolSearch(
            self.agent, run, store=self.store, search_limit=self.search_limit
        )

    def validate_call(self, call: OperationCall) -> None:
        """调用身份只能来自同一受信装配，不从参数或消息中取身份。"""
        identity = call.identity
        if (
            self.closed
            or not self.run
            or (identity.project_id, identity.agent, identity.run)
            != (self.store.project_id, self.agent, self.run)
            or self.store.current_run(self.agent) != self.run
        ):
            raise AccessError("access_denied")

    def _ability(self, tool: SurfaceTool) -> Any:
        """只匹配本次物化的同一工具、实例、别名、成员与版本。"""
        for ability in self.table:
            reference = getattr(ability, "member_reference", None)
            if (
                reference is not None
                and ability.name == tool.entry.tool_name
                and (
                    reference.instance_id,
                    reference.alias,
                    reference.member,
                    reference.version_digest,
                )
                == (
                    tool.entry.member.instance_id,
                    tool.entry.package,
                    tool.entry.member.member,
                    tool.version_digest,
                )
            ):
                return ability
        return None

    @staticmethod
    def _generation(tool: SurfaceTool) -> tuple:
        return (
            tool.entry.member.instance_id,
            tool.entry.package,
            tool.entry.member.member,
            tool.version_digest,
        )

    async def _catalog(self) -> Any:
        if self.closed:
            raise AccessError("access_denied")
        if self.table.ability_content is not None:
            await self.table.ability_content.view()  # 先更新受信 MCP 后端可用性。
        return await read_agent_catalog(self.agent, self.run, store=self.store)

    async def refresh(self, max_context_length: int | None = None) -> list[dict[str, Any]]:
        """每轮从当前授权与执行对象装配；撤销、版本改变和停止会清理加载集。"""
        catalog = await self._catalog()
        ready = {
            tool.entry.tool_name: tool
            for tool in catalog.tools
            if tool.entry.available and self._ability(tool) is not None
        }
        for name in list(self._loaded):
            if name not in ready or self._loaded[name] != self._generation(ready[name]):
                self._loaded.pop(name)
                self._lru.pop(name, None)
        meta = [ready[name] for name in sorted(META_TOOL_NAMES) if name in ready]
        regular = [tool for name, tool in ready.items() if name not in RESERVED_TOOL_NAMES]
        controls = [
            ability.bind_tool()
            for ability in self.table
            if getattr(ability, "member_reference", None) is None
            and ability.name != "run_script"
            and ability.bind_tool() is not None
        ]
        view = await self.store.view(
            AccessIdentity(self.store.project_id, self.agent, self.run), self.agent
        )
        scripts = any(
            script.available
            for node in view.abilities
            for skill in node.skills
            for script in skill.scripts
        )
        extras = controls + ([RunScriptTool(self).bind_tool()] if scripts else [])
        config = self.config
        if len(meta) != len(META_TOOL_NAMES):
            # 显式撤销元工具后退回全量静态；仍受固定 64 项上限约束。
            config = config.model_copy(update={"mode": "static"})
        assembly = assemble_surface(
            regular,
            config=config,
            thresholds=self.thresholds,
            max_context_length=max_context_length,
            reserved_definition_tokens=_cost(extras),
        )
        self.progressive = assembly.mode == "progressive"
        if self.progressive:
            extras += [tool.definition() for tool in meta]
            assembly = assemble_surface(
                regular,
                config=config,
                thresholds=self.thresholds,
                max_context_length=max_context_length,
                reserved_definition_tokens=_cost(extras),
            )
        static_names = {entry.tool_name for entry in assembly.static}
        self._static_names = static_names
        for name in static_names & self._loaded.keys():
            self._loaded.pop(name)
            self._lru.pop(name, None)
        static = [ready[entry.tool_name].definition() for entry in assembly.static]
        loaded = []
        remaining = max(0, assembly.token_budget - _cost(static))
        for name in self._loaded:
            if name in static_names or not self.progressive:
                continue
            definition = ready[name].definition()
            cost = _cost([definition])
            if cost <= remaining:
                loaded.append(definition)
                remaining -= cost
        return static + loaded + extras

    def _touch(self, tool: SurfaceTool, *, load: bool = False) -> None:
        name = tool.entry.tool_name
        if (
            load
            and self.loaded_limit
            and name not in self._loaded
            and name not in self._static_names
        ):
            self._loaded[name] = self._generation(tool)
        if name in self._loaded:
            self._lru.pop(name, None)
            self._lru[name] = None
        while len(self._loaded) > self.loaded_limit:
            oldest, _ = self._lru.popitem(last=False)
            self._loaded.pop(oldest)

    def drain_audit(self) -> list[dict[str, Any]]:
        """交给本轮 ActionChain 元数据记录包装与目标调用，不包含凭据或参数。"""
        rows, self._audit = self._audit, []
        return rows

    async def invoke(
        self, name: str, request: BaseModel, call: OperationCall, services: Any
    ) -> ActionResult:
        """三个元工具只消费当前目录；目标仍经原执行器接纳、审批和执行。"""
        try:
            self.validate_call(call)
            catalog = await self._catalog()
            wrapper = next((tool for tool in catalog.tools if tool.entry.tool_name == name), None)
            if (
                wrapper is None
                or not wrapper.entry.available
                or self._ability(wrapper) is None
                or self._ability(wrapper).member_reference != call.member
            ):
                return _failure("Meta-tool is no longer granted; refresh the tool list")
            self._audit.append(
                {"kind": "meta.call", "name": name, "tool_call_id": call.tool_call_id}
            )
            if name == "search_tools":
                rows = await self._search.search(
                    request.query,
                    limit=request.limit if "limit" in request.model_fields_set else None,
                )
                return ActionResult(
                    ActionOutcome.SUCCESS,
                    {
                        "tools": [
                            {
                                "name": row.tool_name,
                                "description": row.description,
                                "package": row.package,
                            }
                            for row in rows
                        ]
                    },
                )
            target = next(
                (
                    tool
                    for tool in catalog.tools
                    if tool.entry.tool_name == request.name
                    and tool.entry.tool_name not in RESERVED_TOOL_NAMES
                ),
                None,
            )
            if target is None:
                return _failure(
                    "Tool not found in the current grant and selection; use search_tools"
                )
            ability = self._ability(target)
            available = target.entry.available and ability is not None
            if name == "describe_tool":
                if available:
                    self._touch(target, load=True)
                return ActionResult(
                    ActionOutcome.SUCCESS,
                    {
                        "name": target.entry.tool_name,
                        "package": target.entry.package,
                        "description": target.description,
                        "input_schema": target.input_schema,
                        "output_schema": target.output_schema or {},
                        "effects": list(target.effects),
                        "available": available,
                        "skills": list(target.skills),
                    },
                )
            if not available:
                return _failure(
                    "Tool is unavailable; restore its backend or rematerialize this Agent"
                )
            if self.executor is None:
                return _failure("Target executor is not wired")
            self._touch(target)
            routed = replace(call, member=ability.member_reference, arguments=request.arguments)
            result = await self.executor.execute_routed(ability, routed, services)
            self._audit.append(
                {
                    "kind": "tool.call",
                    "name": target.entry.tool_name,
                    "member": asdict(routed.member),
                    "tool_call_id": call.tool_call_id,
                    "outcome": result.outcome.value,
                }
            )
            return result
        except AccessError:
            return _failure("Tool surface access denied; check the current Agent/run and grants")
        except Exception:
            logger.exception("Tool surface invocation failed: %s", name)
            return _failure("Tool call failed; check the target backend and runtime logs")

    async def run_script(self, call: OperationCall) -> ActionResult:
        """复用已有脚本运行器；受信身份由本次装配固定。"""
        try:
            self.validate_call(call)
            request = ScriptInput.model_validate(call.arguments)
        except AccessError, ValueError:
            return _failure("Invalid script request or stale Agent/run")
        result = await execute_run_script(
            store=self.store,
            actor=AccessIdentity(self.store.project_id, self.agent, self.run),
            agent=self.agent,
            source=self.source,
            script=request.script,
            arguments=request.arguments,
            tool_call_id=call.tool_call_id,
        )
        self._audit.append(
            {
                "kind": "script.call",
                "script": request.script,
                "tool_call_id": call.tool_call_id,
                "outcome": result.outcome.value,
            }
        )
        return result

    async def close(self) -> None:
        """运行结束时释放加载与搜索状态，旧对象永久停止接纳。"""
        self.closed = True
        self._loaded.clear()
        self._static_names.clear()
        self._lru.clear()
        self._search = None
        self._audit.clear()


class ScriptInput(BaseModel):
    """脚本入口只接受模型地址和 JSON 对象参数。"""

    model_config = ConfigDict(extra="forbid")
    script: str = Field(description="Registered skills/<skill>/scripts/<key> address.")
    arguments: dict[str, Any] = Field(
        description="JSON object passed to the registered Python script."
    )


class RunScriptTool(Ability):
    """独立脚本入口，不是 core Operation，也不能被 execute_tool 路由。"""

    def __init__(self, surface: ToolSurfaceRuntime) -> None:
        self.surface = surface

    @property
    def name(self) -> str:
        """返回独立入口的固定名称。"""
        return "run_script"

    def bind_tool(self) -> dict[str, Any]:
        """返回脚本工具的模型契约。"""
        return {
            "type": "function",
            "function": {
                "name": self.name,
                "description": "Run a registered Python script from a granted Skill.",
                "parameters": ScriptInput.model_json_schema(),
            },
        }

    def get_hooks(self) -> list[Any]:
        """脚本授权由已有运行器负责，不借用 shell 的审批。"""
        return []

    async def execute(self, call: OperationCall, services: Any) -> ActionResult:
        """将调用与结果留在普通 tool-result 通路。"""
        return await self.surface.run_script(call)
