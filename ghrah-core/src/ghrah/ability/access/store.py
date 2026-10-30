# SPDX-License-Identifier: Apache-2.0
"""同一项目数据库中的资源关系写者；查询只读取已提交版本。"""

from __future__ import annotations

import asyncio
import hashlib
import json
from collections.abc import AsyncIterator, Callable
from contextlib import asynccontextmanager
from dataclasses import dataclass
from pathlib import Path
from typing import Any

import aiosqlite
from ghrah.protocol.ability_tree import (
    AbilityNode,
    AbilityTreeView,
    BindToolRequest,
    BoundToolNode,
    GrantAbilityRequest,
    OperationNode,
    RevokeAbilityRequest,
    TreeErrorCode,
    TreeId,
    UnbindToolRequest,
)
from pydantic import ConfigDict, TypeAdapter

from ghrah.ability.contracts import operation_result_schema
from ghrah.ability.file_members import FILE_OPERATIONS
from ghrah.ability.versions import AbilityVersion

_SCHEMA = """
CREATE TABLE IF NOT EXISTS ability_tree_schema (version INTEGER PRIMARY KEY CHECK(version=2));
INSERT OR IGNORE INTO ability_tree_schema VALUES(2);
CREATE TABLE IF NOT EXISTS ability_versions (
 project TEXT NOT NULL, digest TEXT NOT NULL, definition TEXT NOT NULL,
 implementation TEXT NOT NULL,
 PRIMARY KEY(project,digest));
CREATE TABLE IF NOT EXISTS ability_instances (
 project TEXT NOT NULL, id TEXT NOT NULL, digest TEXT NOT NULL, root TEXT NOT NULL,
 revision INTEGER NOT NULL, PRIMARY KEY(project,id));
CREATE TABLE IF NOT EXISTS ability_agents (
 project TEXT NOT NULL, id TEXT NOT NULL, revision INTEGER NOT NULL,
 PRIMARY KEY(project,id));
CREATE TABLE IF NOT EXISTS ability_grants (
 project TEXT NOT NULL, agent TEXT NOT NULL, alias TEXT NOT NULL, instance TEXT NOT NULL,
 members TEXT NOT NULL, roots TEXT NOT NULL, revision INTEGER NOT NULL,
 PRIMARY KEY(project,agent,alias));
CREATE TABLE IF NOT EXISTS ability_tools (
 project TEXT NOT NULL, agent TEXT NOT NULL, name TEXT NOT NULL, alias TEXT NOT NULL,
 member TEXT NOT NULL, grant_revision INTEGER NOT NULL, revision INTEGER NOT NULL,
 PRIMARY KEY(project,agent,name));
CREATE TABLE IF NOT EXISTS ability_changes (
 seq INTEGER PRIMARY KEY AUTOINCREMENT, project TEXT NOT NULL, agent TEXT NOT NULL,
 revision INTEGER NOT NULL, reason TEXT NOT NULL, actor TEXT,
 before_revision INTEGER, version_digest TEXT);
CREATE INDEX IF NOT EXISTS idx_ability_changes_project_seq ON ability_changes(project,seq);
"""


@dataclass(frozen=True)
class AccessIdentity:
    """受信调用方提供的身份；operator 资格还须匹配宿主项目配置。"""

    project_id: str
    subject_id: str
    run_id: str
    operator: bool = False


@dataclass(frozen=True)
class MemberFence:
    """确定实例、获授关系及工具选择的代次，不随同名关系复活。"""

    __pydantic_config__ = ConfigDict(strict=True, extra="forbid")

    instance_id: str
    instance_revision: int
    member: str
    agent_id: str = "_"
    run_id: str = "_"
    alias: str = "_"
    grant_revision: int = 0
    tool_name: str = "_"
    tool_revision: int = 0


class AccessError(ValueError):
    """可映射为领域结果的明确拒绝。"""

    def __init__(self, code: TreeErrorCode) -> None:
        self.code = code
        super().__init__(code)


class AbilityAccessStore:
    """项目关系的唯一写入口；独立连接可与 ContextBackend 使用同一数据库文件。"""

    def __init__(
        self,
        db: aiosqlite.Connection,
        project_id: str,
        operators: frozenset[str],
        runs: Callable[[str], str | None],
    ) -> None:
        self._db = db
        self.project_id = TypeAdapter(TreeId).validate_python(project_id)
        self._operators = operators
        self._runs = runs
        self._lock = asyncio.Lock()

    @classmethod
    async def open(
        cls,
        path: str | Path,
        project_id: str,
        *,
        operators: frozenset[str],
        runs: Callable[[str], str | None],
    ) -> AbilityAccessStore:
        """显式初始化项目表；不修改既有上下文 schema 或启动 Agent。"""
        db = await aiosqlite.connect(str(path), isolation_level=None)
        db.row_factory = aiosqlite.Row
        try:
            await db.execute("PRAGMA busy_timeout=5000")
            await db.execute("BEGIN IMMEDIATE")
            try:
                async with db.execute(
                    "SELECT name FROM sqlite_master WHERE name='ability_tree_schema'"
                ) as cursor:
                    exists = await cursor.fetchone()
                if exists:
                    async with db.execute("SELECT version FROM ability_tree_schema") as cursor:
                        version = await cursor.fetchone()
                    if version is None or version[0] not in {1, 2}:
                        raise ValueError("不支持的 Ability 关系数据库版本")
                    if version[0] == 1:
                        await db.execute("ALTER TABLE ability_agents DROP COLUMN run")
                        await db.execute("ALTER TABLE ability_changes ADD COLUMN actor TEXT")
                        await db.execute(
                            "ALTER TABLE ability_changes ADD COLUMN before_revision INTEGER"
                        )
                        await db.execute(
                            "ALTER TABLE ability_changes ADD COLUMN version_digest TEXT"
                        )
                        await db.execute("DROP TABLE ability_tree_schema")
                for statement in _SCHEMA.split(";"):
                    if statement.strip():
                        await db.execute(statement)
                await db.commit()
            except BaseException:
                await db.rollback()
                raise
            return cls(db, project_id, operators, runs)
        except BaseException:
            await db.close()
            raise

    async def close(self) -> None:
        """关闭自己的连接，不关闭其他领域 owner。"""
        async with self._lock:
            await self._db.close()

    @asynccontextmanager
    async def _tx(self, *, write: bool = False) -> AsyncIterator[None]:
        async with self._lock:
            await self._db.execute("BEGIN IMMEDIATE" if write else "BEGIN")
            try:
                yield
                await self._db.commit()
            except BaseException:
                await self._db.rollback()
                raise

    async def _row(self, table: str, condition: str, *params: object) -> Any:
        # table 和 condition 只由本模块常量提供，不接受调用输入。
        async with self._db.execute(
            f"SELECT * FROM {table} WHERE project=? AND {condition}",
            (self.project_id, *params),
        ) as cursor:
            return await cursor.fetchone()

    def _operator(self, actor: AccessIdentity) -> bool:
        return (
            actor.project_id == self.project_id
            and actor.operator
            and actor.subject_id in self._operators
        )

    def _require_operator(self, actor: AccessIdentity) -> None:
        if not self._operator(actor):
            raise AccessError("access_denied")

    def require_operator(self, actor: AccessIdentity) -> None:
        """核对受信操作员是否获宿主配置允许管理此项目。"""
        self._require_operator(actor)

    def current_run(self, agent: str) -> str:
        """每次直接读取注入的生命周期权威，不在关系库复制 run。"""
        run = self._runs(agent)
        if run is None:
            raise AccessError("unavailable")
        return TypeAdapter(TreeId).validate_python(run)

    async def _agent(self, actor: AccessIdentity, agent: str) -> Any:
        if actor.project_id != self.project_id:
            raise AccessError("access_denied")
        row = await self._row("ability_agents", "id=?", agent)
        if row is None:
            raise AccessError("not_found")
        run = self.current_run(agent)
        if not self._operator(actor) and (
            actor.operator or actor.subject_id != agent or actor.run_id != run
        ):
            raise AccessError("access_denied")
        return row

    async def _bump(
        self,
        actor: AccessIdentity,
        agent: str,
        expected: int,
        reason: str,
        version_digest: str | None = None,
    ) -> int:
        row = await self._row("ability_agents", "id=?", agent)
        if row is None or row["revision"] != expected:
            raise AccessError("conflict")
        revision = expected + 1
        await self._db.execute(
            "UPDATE ability_agents SET revision=? WHERE project=? AND id=?",
            (revision, self.project_id, agent),
        )
        await self._db.execute(
            "INSERT INTO ability_changes(project,agent,revision,reason,actor,"
            "before_revision,version_digest) VALUES(?,?,?,?,?,?,?)",
            (
                self.project_id,
                agent,
                revision,
                reason,
                json.dumps({"subject": actor.subject_id, "run": actor.run_id}),
                expected,
                version_digest,
            ),
        )
        return revision

    async def commit_version(self, actor: AccessIdentity, version: AbilityVersion) -> None:
        """提交确定定义；相同版本幂等，不扫描来源或自动给 Agent 授权。"""
        self._require_operator(actor)
        expected = (
            "sha256:"
            + hashlib.sha256(
                (version.definition_json + version.implementation_digest).encode()
            ).hexdigest()
        )
        if expected != version.digest:
            raise AccessError("invalid_change")
        async with self._tx(write=True):
            await self._db.execute(
                "INSERT OR IGNORE INTO ability_versions VALUES(?,?,?,?)",
                (
                    self.project_id,
                    version.digest,
                    version.definition_json,
                    version.implementation_digest,
                ),
            )

    async def register_agent(
        self, actor: AccessIdentity, agent: str, *, expected_revision: int = 0
    ) -> None:
        """登记关系修订，运行身份始终由外部生命周期 owner 提供。"""
        self._require_operator(actor)
        TypeAdapter(TreeId).validate_python(agent)
        self.current_run(agent)
        async with self._tx(write=True):
            row = await self._row("ability_agents", "id=?", agent)
            if row is None:
                if expected_revision:
                    raise AccessError("conflict")
                await self._db.execute(
                    "INSERT INTO ability_agents VALUES(?,?,0)", (self.project_id, agent)
                )
            elif row["revision"] != expected_revision:
                raise AccessError("conflict")

    async def select_instance(
        self,
        actor: AccessIdentity,
        instance: str,
        digest: str,
        root: Path,
        *,
        expected_revision: int = 0,
    ) -> int:
        """显式切换实例版本；实际变化撤去旧获授关联，禁止旧许可自动扩大。"""
        self._require_operator(actor)
        TypeAdapter(TreeId).validate_python(instance)
        resolved = str(root.resolve())
        async with self._tx(write=True):
            if await self._row("ability_versions", "digest=?", digest) is None:
                raise AccessError("not_found")
            old = await self._row("ability_instances", "id=?", instance)
            if (old["revision"] if old else 0) != expected_revision:
                raise AccessError("conflict")
            if old and old["digest"] == digest and old["root"] == resolved:
                return old["revision"]
            revision = expected_revision + 1
            await self._db.execute(
                "INSERT OR REPLACE INTO ability_instances VALUES(?,?,?,?,?)",
                (self.project_id, instance, digest, resolved, revision),
            )
            async with self._db.execute(
                "SELECT agent,alias FROM ability_grants WHERE project=? AND instance=?",
                (self.project_id, instance),
            ) as cursor:
                affected = await cursor.fetchall()
            for grant in affected:
                row = await self._row("ability_agents", "id=?", grant["agent"])
                await self._bump(actor, grant["agent"], row["revision"], "instance_changed", digest)
                await self._remove_grant(grant["agent"], grant["alias"])
            return revision

    async def instance(self, instance: str) -> dict[str, Any]:
        """受信装配读取已提交实例；不重扫或推进版本。"""
        async with self._tx():
            row = await self._row("ability_instances", "id=?", instance)
            if row is None:
                raise AccessError("not_found")
            version = await self._row("ability_versions", "digest=?", row["digest"])
            return {
                **dict(row),
                "definition": json.loads(version["definition"]),
                "implementation_digest": version["implementation"],
            }

    async def _remove_grant(self, agent: str, alias: str) -> None:
        await self._db.execute(
            "DELETE FROM ability_tools WHERE project=? AND agent=? AND alias=?",
            (self.project_id, agent, alias),
        )
        await self._db.execute(
            "DELETE FROM ability_grants WHERE project=? AND agent=? AND alias=?",
            (self.project_id, agent, alias),
        )

    async def grant(
        self, actor: AccessIdentity, agent: str, change: GrantAbilityRequest
    ) -> AbilityTreeView:
        """同一事务提交获授范围和历史；替换关系使旧工具选择失效。"""
        self._require_operator(actor)
        async with self._tx(write=True):
            current = await self._agent(actor, agent)
            if current["revision"] != change.expected_revision:
                raise AccessError("conflict")
            instance = await self._row("ability_instances", "id=?", change.instance_id)
            if instance is None:
                raise AccessError("not_found")
            version = await self._row("ability_versions", "digest=?", instance["digest"])
            definition = json.loads(version["definition"])
            if any(not Path(root).is_absolute() for root in change.allowed_roots):
                raise AccessError("invalid_change")
            roots = sorted({str(Path(root).resolve()) for root in change.allowed_roots})
            exposed = {
                key for key, operation in definition["operations"].items() if operation["expose"]
            }
            if not set(change.members) <= exposed or not all(
                Path(root).is_relative_to(instance["root"]) for root in roots
            ):
                raise AccessError("invalid_change")
            previous = await self._row("ability_grants", "agent=? AND alias=?", agent, change.alias)
            if previous and (
                previous["instance"] == change.instance_id
                and json.loads(previous["members"]) == sorted(set(change.members))
                and json.loads(previous["roots"]) == roots
            ):
                return await self._view(agent)
            revision = await self._bump(
                actor, agent, change.expected_revision, "grant", instance["digest"]
            )
            await self._remove_grant(agent, change.alias)
            await self._db.execute(
                "INSERT INTO ability_grants VALUES(?,?,?,?,?,?,?)",
                (
                    self.project_id,
                    agent,
                    change.alias,
                    change.instance_id,
                    json.dumps(sorted(set(change.members))),
                    json.dumps(roots),
                    revision,
                ),
            )
            return await self._view(agent)

    async def revoke(
        self, actor: AccessIdentity, agent: str, change: RevokeAbilityRequest
    ) -> AbilityTreeView:
        """撤销获授及其工具选择，不停止共享实例。"""
        self._require_operator(actor)
        async with self._tx(write=True):
            current = await self._agent(actor, agent)
            if current["revision"] != change.expected_revision:
                raise AccessError("conflict")
            if (
                await self._row("ability_grants", "agent=? AND alias=?", agent, change.alias)
                is None
            ):
                return await self._view(agent)
            await self._bump(actor, agent, change.expected_revision, "revoke")
            await self._remove_grant(agent, change.alias)
            return await self._view(agent)

    async def bind_tool(
        self, actor: AccessIdentity, agent: str, change: BindToolRequest
    ) -> AbilityTreeView:
        """只从本 Agent 当前获授集合选择工具，不能增加授权。"""
        async with self._tx(write=True):
            current = await self._agent(actor, agent)
            if current["revision"] != change.expected_revision:
                raise AccessError("conflict")
            grant = await self._row("ability_grants", "agent=? AND alias=?", agent, change.alias)
            if grant is None or change.member not in json.loads(grant["members"]):
                raise AccessError("access_denied")
            instance = await self._row("ability_instances", "id=?", grant["instance"])
            version = await self._row("ability_versions", "digest=?", instance["digest"])
            operation = json.loads(version["definition"])["operations"].get(change.member)
            if operation is None or not operation["expose"]:
                raise AccessError("access_denied")
            previous = await self._row(
                "ability_tools", "agent=? AND name=?", agent, change.tool_name
            )
            if previous and (
                previous["alias"] == change.alias
                and previous["member"] == change.member
                and previous["grant_revision"] == grant["revision"]
            ):
                return await self._view(agent)
            revision = await self._bump(actor, agent, change.expected_revision, "bind_tool")
            await self._db.execute(
                "INSERT OR REPLACE INTO ability_tools VALUES(?,?,?,?,?,?,?)",
                (
                    self.project_id,
                    agent,
                    change.tool_name,
                    change.alias,
                    change.member,
                    grant["revision"],
                    revision,
                ),
            )
            return await self._view(agent)

    async def unbind_tool(
        self, actor: AccessIdentity, agent: str, change: UnbindToolRequest
    ) -> AbilityTreeView:
        """删除工具选择，保留获授关系。"""
        async with self._tx(write=True):
            current = await self._agent(actor, agent)
            if current["revision"] != change.expected_revision:
                raise AccessError("conflict")
            if (
                await self._row("ability_tools", "agent=? AND name=?", agent, change.tool_name)
                is None
            ):
                return await self._view(agent)
            await self._bump(actor, agent, change.expected_revision, "unbind_tool")
            await self._db.execute(
                "DELETE FROM ability_tools WHERE project=? AND agent=? AND name=?",
                (self.project_id, agent, change.tool_name),
            )
            return await self._view(agent)

    async def _view(self, agent: str) -> AbilityTreeView:
        row = await self._row("ability_agents", "id=?", agent)
        abilities = []
        async with self._db.execute(
            "SELECT * FROM ability_grants WHERE project=? AND agent=? ORDER BY alias",
            (self.project_id, agent),
        ) as cursor:
            grants = await cursor.fetchall()
        for grant in grants:
            instance = await self._row("ability_instances", "id=?", grant["instance"])
            version = await self._row("ability_versions", "digest=?", instance["digest"])
            definition = json.loads(version["definition"])
            members = json.loads(grant["members"])
            abilities.append(
                AbilityNode(
                    alias=grant["alias"],
                    instance_id=instance["id"],
                    instance_revision=instance["revision"],
                    definition_digest="sha256:"
                    + hashlib.sha256(version["definition"].encode()).hexdigest(),
                    version_digest=instance["digest"],
                    revision=grant["revision"],
                    allowed_roots=json.loads(grant["roots"]),
                    operations=[
                        OperationNode(
                            key=key,
                            description=definition["operations"][key]["description"],
                            input_schema=definition["operations"][key]["input_schema"],
                            output_schema=operation_result_schema(
                                definition["operations"][key]["output_schema"]
                            ),
                        )
                        for key in members
                        if definition["operations"][key]["expose"]
                    ],
                )
            )
        async with self._db.execute(
            "SELECT * FROM ability_tools WHERE project=? AND agent=? ORDER BY name",
            (self.project_id, agent),
        ) as cursor:
            tools = await cursor.fetchall()
        visible = {node.alias: {item.key for item in node.operations} for node in abilities}
        return AbilityTreeView(
            project_id=self.project_id,
            agent_id=agent,
            run_id=self.current_run(agent),
            revision=row["revision"],
            abilities=abilities,
            bound_tools=[
                BoundToolNode(
                    name=tool["name"],
                    alias=tool["alias"],
                    member=tool["member"],
                    grant_revision=tool["grant_revision"],
                    revision=tool["revision"],
                )
                for tool in tools
                if tool["member"] in visible.get(tool["alias"], set())
            ],
        )

    async def view(self, actor: AccessIdentity, agent: str) -> AbilityTreeView:
        """事务内读取同源投影，不扫描来源、不更新状态。"""
        async with self._tx():
            await self._agent(actor, agent)
            return await self._view(agent)

    async def resolve(
        self,
        actor: AccessIdentity,
        alias: str,
        member: str,
        *,
        tool: str | None = None,
        global_instance: bool = False,
    ) -> MemberFence:
        """从显式全局实例或当前 Agent 关联固定成员和代次。"""
        async with self._tx():
            if global_instance:
                self._require_operator(actor)
                instance = await self._row("ability_instances", "id=?", alias)
                if instance is None:
                    raise AccessError("not_found")
                fence = MemberFence(alias, instance["revision"], member)
            else:
                await self._agent(actor, actor.subject_id)
                grant = await self._row(
                    "ability_grants", "agent=? AND alias=?", actor.subject_id, alias
                )
                if grant is None:
                    raise AccessError("not_found")
                instance = await self._row("ability_instances", "id=?", grant["instance"])
                tool_row = (
                    await self._row("ability_tools", "agent=? AND name=?", actor.subject_id, tool)
                    if tool is not None
                    else None
                )
                if tool is not None and (
                    tool_row is None or tool_row["alias"] != alias or tool_row["member"] != member
                ):
                    raise AccessError("access_denied")
                fence = MemberFence(
                    instance["id"],
                    instance["revision"],
                    member,
                    actor.subject_id,
                    actor.run_id,
                    alias,
                    grant["revision"],
                    tool or "_",
                    tool_row["revision"] if tool_row else 0,
                )
            await self._check(actor, fence)
            return fence

    async def _check(
        self, actor: AccessIdentity, fence: MemberFence, arguments: dict[str, Any] | None = None
    ) -> str:
        instance = await self._row("ability_instances", "id=?", fence.instance_id)
        if instance is None or instance["revision"] != fence.instance_revision:
            raise AccessError("access_denied")
        version = await self._row("ability_versions", "digest=?", instance["digest"])
        operation = json.loads(version["definition"])["operations"].get(fence.member)
        if operation is None or not operation["expose"]:
            raise AccessError("access_denied")
        roots = [instance["root"]]
        if fence.agent_id == "_":
            self._require_operator(actor)
        else:
            await self._agent(actor, fence.agent_id)
            if actor.operator or actor.subject_id != fence.agent_id or actor.run_id != fence.run_id:
                raise AccessError("access_denied")
            grant = await self._row(
                "ability_grants", "agent=? AND alias=?", fence.agent_id, fence.alias
            )
            if (
                grant is None
                or grant["revision"] != fence.grant_revision
                or grant["instance"] != fence.instance_id
                or fence.member not in json.loads(grant["members"])
            ):
                raise AccessError("access_denied")
            roots = json.loads(grant["roots"])
            if fence.tool_name != "_":
                tool = await self._row(
                    "ability_tools", "agent=? AND name=?", fence.agent_id, fence.tool_name
                )
                if (
                    tool is None
                    or tool["revision"] != fence.tool_revision
                    or tool["grant_revision"] != fence.grant_revision
                    or tool["alias"] != fence.alias
                    or tool["member"] != fence.member
                ):
                    raise AccessError("access_denied")
        if arguments is not None:
            member = FILE_OPERATIONS.get(fence.member)
            if member is None or member.path_argument not in arguments:
                raise AccessError("invalid_change")
            path = Path(arguments[member.path_argument])
            resolved = (path if path.is_absolute() else Path(instance["root"]) / path).resolve()
            if not resolved.is_relative_to(instance["root"]) or not any(
                resolved.is_relative_to(root) for root in roots
            ):
                raise AccessError("access_denied")
        return f"{instance['digest']}:{fence.grant_revision}:{fence.tool_revision}"

    async def authorize(self, actor: AccessIdentity, fence: MemberFence) -> str:
        """核对当前关系，不签发另一份许可。"""
        async with self._tx():
            return await self._check(actor, fence)

    async def admit(
        self, actor: AccessIdentity, fence: MemberFence, arguments: dict[str, Any]
    ) -> tuple[str, None]:
        """只读文件成员在读取事务中核定，不抢写锁或持久化调用参数。"""
        async with self._tx():
            if fence.member not in FILE_OPERATIONS:
                raise AccessError("invalid_change")
            return await self._check(actor, fence, arguments), None

    async def list_instances(self, actor: AccessIdentity) -> list[str]:
        """从权威列举当前选定实例，供重启后显式重建导出。"""
        self._require_operator(actor)
        async with self._tx():
            async with self._db.execute(
                "SELECT id FROM ability_instances WHERE project=? ORDER BY id", (self.project_id,)
            ) as cursor:
                return [row[0] for row in await cursor.fetchall()]

    async def list_versions(self, actor: AccessIdentity) -> list[AbilityVersion]:
        """读取已提交版本，不扫描来源或改写当前选择。"""
        self._require_operator(actor)
        async with self._tx():
            async with self._db.execute(
                "SELECT digest,definition,implementation FROM ability_versions "
                "WHERE project=? ORDER BY digest",
                (self.project_id,),
            ) as cursor:
                return [AbilityVersion(*row) for row in await cursor.fetchall()]

    async def changes(
        self, actor: AccessIdentity, *, after_seq: int = 0, limit: int = 100
    ) -> list[dict[str, Any]]:
        """有界读取本项目修订通知；完整重建使用权威表，不回放此通知。"""
        self._require_operator(actor)
        if (
            type(after_seq) is not int
            or after_seq < 0
            or type(limit) is not int
            or not 1 <= limit <= 1000
        ):
            raise AccessError("invalid_change")
        async with self._tx():
            async with self._db.execute(
                "SELECT * FROM ability_changes WHERE project=? AND seq>? ORDER BY seq LIMIT ?",
                (self.project_id, after_seq, limit),
            ) as cursor:
                return [dict(row) for row in await cursor.fetchall()]
