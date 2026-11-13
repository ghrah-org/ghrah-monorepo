"""新资源契约的管理扩展；所有写入仍经 Core 定义表和既有授权库。"""

from __future__ import annotations

import re
from os.path import commonpath
from pathlib import Path
from typing import TYPE_CHECKING, Any

from ghrah.protocol.ability_control import AbilityBoundToolView
from ghrah.protocol.ability_tree import (
    BindToolRequest,
    GrantAbilityRequest,
    RevokeAbilityRequest,
    UnbindToolRequest,
)
from ghrah.protocol.runtime import RuntimeDispatchResponse
from ghrah.protocol.runtime_digest import assembly_digest, canonical_json, normalized_assembly
from ghrah.protocol.runtime_management import (
    RuntimeAbilitiesView,
    RuntimeAbilityCatalog,
    RuntimeAbilityChanges,
    RuntimeAbilityInstance,
    RuntimeAbilityInstanceCreate,
    RuntimeAgentPatch,
    RuntimeGrantRequest,
    RuntimeToolRequest,
)

from ghrah.ability.access import AccessError, AccessIdentity
from ghrah.core.read.ability_admin import build_admin_view, build_change_view
from ghrah.core.read.ability_catalog import build_package_catalog
from ghrah.core.runtime_resource_ids import ResourceError, internal_id, wire_id

if TYPE_CHECKING:
    from ghrah.core.runtime_agent_api import RuntimeAgentAPI

_JSON = {"Content-Type": "application/json"}


class RuntimeManagementAPI:
    """复用资源入口锁、身份、生命周期和存储，不拥有独立状态。"""

    def __init__(self, owner: RuntimeAgentAPI) -> None:
        """绑定已初始化的资源入口。"""
        self.owner = owner
        self.store = owner.ability_store
        self.actor = AccessIdentity(owner.store.scope_ref, "deployment", "", True, "admin")

    @staticmethod
    def _condition(headers: dict[str, str], etag: str) -> None:
        if "if-match" not in headers:
            raise ResourceError(428, None, "If-Match is required")
        if headers["if-match"] != etag:
            raise ResourceError(
                412, "PRECONDITION_FAILED", "Resource revision differs", current_etag=etag
            )

    async def agent_change(
        self, method: str, row: Any, headers: dict[str, str], body: Any
    ) -> RuntimeDispatchResponse:
        """停止后原子替换配置，或先停止再删除定义；不删除历史消息事实。"""
        self._condition(headers, self.owner._etag(row))
        agent_id = row["agent_id"]
        if method == "DELETE":
            await self.owner._state(row, {"state": "stopped"}, headers)
            if not await self.owner.store.delete(agent_id, row["config_revision"]):
                raise ResourceError(412, "PRECONDITION_FAILED", "Resource revision differs")
            return RuntimeDispatchResponse(204, {}, None)
        if await self.owner._registered(agent_id):
            raise ResourceError(
                409, "INVALID_STATE", "Stop the Agent before replacing its configuration"
            )
        request = RuntimeAgentPatch.model_validate(body)
        self.owner._supported(request.config)
        if (
            len(canonical_json(request.config.model_dump(mode="json")).encode())
            > self.owner.config_quota
        ):
            raise ResourceError(413, None, "Agent config exceeds the configured byte quota")
        changed = await self.owner.store.replace_config(
            agent_id,
            normalized_assembly(request.config),
            assembly_digest(request.config),
            row["config_revision"],
        )
        if not changed:
            raise ResourceError(412, "PRECONDITION_FAILED", "Resource revision differs")
        updated = await self.owner.store.get(agent_id)
        return RuntimeDispatchResponse(
            200, {**_JSON, "ETag": self.owner._etag(updated)}, await self.owner._view(updated)
        )

    async def view(self, agent_id: str) -> RuntimeAbilitiesView:
        """从授权权威和实际工具快照组合可重建读面；GET 不写入。"""
        handle = await self.owner.supervisor.get_agent_handle_by_id(agent_id)
        tools = tuple(handle.visible_tool_names()) if handle is not None else None
        admin = await build_admin_view(
            self.store, self.actor, agent_id, runtime_tools=tools, grant_policy=None
        )
        bindings = await self.store.tool_bindings(self.actor, agent_id)
        return RuntimeAbilitiesView(
            agent_id=wire_id("agt", agent_id),
            revision=admin.revision,
            state=admin.lifecycle.state,
            run_id=admin.run_id,
            requires_respawn=admin.lifecycle.requires_respawn,
            grants=admin.grants,
            bound_tools=[
                AbilityBoundToolView.model_validate(tool.model_dump()) for tool in bindings
            ],
            surface=admin.surface,
        )

    async def _instance(self, body: Any) -> dict[str, Any]:
        request = RuntimeAbilityInstanceCreate.model_validate(body)
        if request.package not in self.owner._source.definitions():
            raise ResourceError(404, "NOT_FOUND", "Builtin package does not exist")
        version = self.owner._source.capture_package(request.package)
        instance_id = request.instance_id or request.package.replace(".", "-")
        if any(not Path(root).is_absolute() for root in request.allowed_roots):
            raise ResourceError(422, "INVALID_ARGUMENT", "Allowed roots must be absolute")
        root = Path(commonpath(request.allowed_roots)) if request.allowed_roots else None
        try:
            existing = await self.store.instance(instance_id)
        except AccessError as error:
            if error.code != "not_found":
                raise
            existing = None
        if existing is not None:
            if existing["digest"] != version.digest or existing["root"] != (
                str(root.resolve()) if root else ""
            ):
                raise ResourceError(
                    409, "CONFLICT", "Instance already selects different content or roots"
                )
            revision = int(existing["revision"])
        else:
            await self.store.commit_version(self.actor, version)
            revision = await self.store.select_instance(
                self.actor, instance_id, version.digest, root
            )
        definition = version.definition()
        return RuntimeAbilityInstance(
            instance_id=instance_id,
            package=request.package,
            version_digest=version.digest,
            revision=revision,
            operations=sorted(definition.operations),
            skills=sorted(definition.skills),
        ).model_dump(mode="json")

    async def dispatch(
        self,
        method: str,
        route: str,
        query: dict[str, list[str]],
        headers: dict[str, str],
        body: Any,
    ) -> RuntimeDispatchResponse:
        """派发能力资源并保留条件写、方法错误和领域拒绝语义。"""
        headers = {key.lower(): value for key, value in headers.items()}
        match = re.fullmatch(
            r"/v1/agents/([^/]+)/abilities(?:/(changes|grants|tools)(?:/([^/]+))?)?", route
        )
        allowed = (
            "GET"
            if route == "/v1/abilities"
            else "POST"
            if route == "/v1/ability-instances"
            else None
        )
        if match:
            allowed = (
                "GET" if match[2] in {None, "changes"} else "PUT, DELETE" if match[3] else None
            )
        if allowed is None:
            raise ResourceError(404, "NOT_FOUND", "Resource does not exist")
        if method not in allowed.split(", "):
            raise ResourceError(405, None, "Method is not allowed", allow=allowed)
        if query and (not match or match[2] != "changes"):
            raise ResourceError(422, "INVALID_ARGUMENT", "Query parameters are not supported here")
        try:
            if route == "/v1/abilities":
                catalog = build_package_catalog(None)
                result = RuntimeAbilityCatalog(
                    packages=[item for item in catalog.packages if item.origin == "builtin"]
                )
                return RuntimeDispatchResponse(200, dict(_JSON), result.model_dump(mode="json"))
            if route == "/v1/ability-instances":
                return RuntimeDispatchResponse(200, dict(_JSON), await self._instance(body))
            agent_id = internal_id("agt", match[1])
            if await self.owner.store.get(agent_id) is None:
                raise ResourceError(404, "NOT_FOUND", "Agent does not exist")
            if match[2] == "changes":
                if set(query) - {"after_seq", "limit"} or any(
                    len(value) != 1 for value in query.values()
                ):
                    raise ResourceError(
                        422, "INVALID_ARGUMENT", "Unknown or repeated query parameter"
                    )
                after, limit = (
                    int(query.get("after_seq", ["0"])[0]),
                    int(query.get("limit", ["100"])[0]),
                )
                rows = await self.store.changes(self.actor, after_seq=after, limit=limit)
                result = RuntimeAbilityChanges(
                    changes=[build_change_view(row) for row in rows if row["agent"] == agent_id],
                    next_after_seq=rows[-1]["seq"] if rows else after,
                    has_more=len(rows) == limit,
                )
                return RuntimeDispatchResponse(200, dict(_JSON), result.model_dump(mode="json"))
            current = await self.view(agent_id)
            if method != "GET":
                self._condition(headers, f'"ability-{current.revision}"')
                await self.store.ensure_agent(self.actor, agent_id)
                if match[2] == "grants":
                    if method == "PUT":
                        change = RuntimeGrantRequest.model_validate(body)
                        if change.alias != match[3]:
                            raise ResourceError(
                                422, "INVALID_ARGUMENT", "Grant alias differs from resource path"
                            )
                        await self.store.write_grant(
                            self.actor,
                            agent_id,
                            GrantAbilityRequest(
                                **change.model_dump(), expected_revision=current.revision
                            ),
                        )
                    else:
                        await self.store.write_revoke(
                            self.actor,
                            agent_id,
                            RevokeAbilityRequest(
                                alias=match[3], expected_revision=current.revision
                            ),
                        )
                elif method == "PUT":
                    tool = RuntimeToolRequest.model_validate(body)
                    if tool.tool_name != match[3]:
                        raise ResourceError(
                            422, "INVALID_ARGUMENT", "Tool name differs from resource path"
                        )
                    await self.store.write_bind_tool(
                        self.actor,
                        agent_id,
                        BindToolRequest(**tool.model_dump(), expected_revision=current.revision),
                    )
                else:
                    await self.store.write_unbind_tool(
                        self.actor,
                        agent_id,
                        UnbindToolRequest(tool_name=match[3], expected_revision=current.revision),
                    )
                current = await self.view(agent_id)
            return RuntimeDispatchResponse(
                200,
                {**_JSON, "ETag": f'"ability-{current.revision}"'},
                current.model_dump(mode="json"),
            )
        except AccessError as error:
            status, code = {
                "conflict": (412, "PRECONDITION_FAILED"),
                "access_denied": (403, "ACCESS_DENIED"),
                "not_found": (404, "NOT_FOUND"),
                "unavailable": (503, "UNAVAILABLE"),
            }.get(error.code, (422, "INVALID_ARGUMENT"))
            raise ResourceError(status, code, "Ability operation was rejected") from error
