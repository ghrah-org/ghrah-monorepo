# SPDX-FileCopyrightText: 2026 chenxya <chenxya@ghrah.org>
#
# SPDX-License-Identifier: Apache-2.0
"""项目资源关系唯一写者；定义读提交快照，来源可用性由宿主显式提供。"""

from __future__ import annotations

import asyncio
import hashlib
import json
import re
from collections.abc import AsyncIterator, Callable
from contextlib import asynccontextmanager
from contextvars import ContextVar
from dataclasses import dataclass, replace
from pathlib import Path
from typing import Any, Literal
from uuid import uuid4

import aiosqlite
from ghrah.protocol.ability_tree import (
    AbilityNode,
    AbilityTreeView,
    BindToolRequest,
    BoundToolNode,
    ContentResourceNode,
    GrantAbilityRequest,
    OperationNode,
    RevokeAbilityRequest,
    SkillNode,
    TreeId,
    UnbindToolRequest,
)
from pydantic import ConfigDict, TypeAdapter

from ghrah.ability.access.backends import admission_for, file_member_paths
from ghrah.ability.access.errors import AccessError
from ghrah.ability.access.facts import request_facts, stored_facts
from ghrah.ability.content import (
    ContentChunk,
    ContentDescription,
    ContentReadRequest,
    ContentReadResult,
    digest_bytes,
)
from ghrah.ability.contracts import operation_result_schema
from ghrah.ability.provider_contracts import PYTHON_CONTRACT
from ghrah.ability.versions import AbilityVersion

_SCHEMA = """
CREATE TABLE IF NOT EXISTS ability_tree_schema (version INTEGER PRIMARY KEY CHECK(version=5));
INSERT OR IGNORE INTO ability_tree_schema VALUES(5);
CREATE TABLE IF NOT EXISTS ability_versions (
 project TEXT NOT NULL, digest TEXT NOT NULL, definition TEXT NOT NULL,
 implementation TEXT NOT NULL, manifest TEXT NOT NULL DEFAULT '{}',
 PRIMARY KEY(project,digest));
CREATE TABLE IF NOT EXISTS ability_content (
 project TEXT NOT NULL, digest TEXT NOT NULL, data BLOB NOT NULL,
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
 content TEXT NOT NULL DEFAULT '[]', axes TEXT NOT NULL DEFAULT '{}',
 source TEXT NOT NULL DEFAULT 'admin',
 PRIMARY KEY(project,agent,alias));
CREATE TABLE IF NOT EXISTS ability_tools (
 project TEXT NOT NULL, agent TEXT NOT NULL, name TEXT NOT NULL, alias TEXT NOT NULL,
 member TEXT NOT NULL, grant_revision INTEGER NOT NULL, revision INTEGER NOT NULL,
 PRIMARY KEY(project,agent,name));
CREATE TABLE IF NOT EXISTS ability_changes (
 seq INTEGER PRIMARY KEY AUTOINCREMENT, project TEXT NOT NULL, agent TEXT NOT NULL,
 revision INTEGER NOT NULL, reason TEXT NOT NULL, actor TEXT,
 before_revision INTEGER, version_digest TEXT,
 source TEXT NOT NULL DEFAULT 'admin', policy TEXT);
CREATE TABLE IF NOT EXISTS ability_registrations (
 project TEXT NOT NULL, agent TEXT NOT NULL, token TEXT NOT NULL, state TEXT NOT NULL,
 owned_revision INTEGER NOT NULL, PRIMARY KEY(project,agent));
CREATE INDEX IF NOT EXISTS idx_ability_changes_project_seq ON ability_changes(project,seq);
"""


@dataclass
class StartupRegistration:
    """一次独占启动的提交标记；具体关系快照仅由关系库管理。"""

    committed: bool = False


GrantSource = Literal["admin", "deployment", "agent-default", "migration"]
"""关系库写入来源：管理员写入、启动核定、Agent 默认集、一次性迁移。"""

MIGRATION_SOURCE = "migration"
"""一次性迁移的写入来源；也是“已迁移”的幂等标记。"""

_GRANT_SOURCES = frozenset({"admin", "deployment", "agent-default", "migration"})


@dataclass(frozen=True)
class AccessIdentity:
    """受信调用方提供的身份；operator 资格还须匹配宿主项目配置。

    ``source`` 记录这一次写入的来源，进入关系库与变更历史，便于审计区分
    “管理员明确授予”“启动核定物化”“迁移补齐”与“Agent 默认集”。
    """

    project_id: str
    subject_id: str
    run_id: str
    operator: bool = False
    source: GrantSource = "admin"

    def __post_init__(self) -> None:
        if self.source not in _GRANT_SOURCES:
            raise ValueError(f"未知的授权写入来源：{self.source!r}")


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
    version_digest: str = ""


_SCRIPT_GRANT_ADDRESS = re.compile(
    r"^skill/(?P<skill>[a-z0-9]+(?:-[a-z0-9]+)*)/scripts/(?P<key>[a-z0-9][a-z0-9._-]*)$"
)
"""授予侧脚本地址：``skill/<skill>/scripts/<key>``。模型地址用复数 ``skills/``。"""


@dataclass(frozen=True)
class ScriptGrant:
    """一次可继续做文件核对的脚本授予。

    这里只固定关系库能确定的事实：哪个包、哪一版、工作区、登记路径与摘要。
    包目录是否仍在、文件是不是链接，由脚本运行器在起进程前再查。

    Attributes:
        alias: 命中的能力别名。
        instance_id: 授予所绑定的实例。
        version_digest: 该实例当前版本摘要。
        grant_address: ``skill/<skill>/scripts/<key>``。
        workspace: 实例根，也是子进程工作目录；不是包目录。
        package: 包身份全名。
        release: 发布版本号。
        container: 内置包目录名。
        namespace: 包命名空间。
        relative_path: 索引里的包内相对路径。
        digest: 授予版本清单中的内容摘要。
        size: 授予版本清单中的字节数。
    """

    alias: str
    instance_id: str
    version_digest: str
    grant_address: str
    workspace: str
    package: str
    release: str
    container: str
    namespace: str
    relative_path: str
    digest: str
    size: int


@dataclass(frozen=True)
class GrantRecord:
    """关系库里的一行获授事实（启动核定读取的原始形状）。

    Attributes:
        alias: Agent 内的能力别名。
        instance_id: 成员所属实例。
        members: 获授的 Operation 键。
        content: 获授的内容成员地址。
        roots: 文件类成员的允许根。
        axes: 成员地址 → 可见/可用事实。
        revision: 该行写入时的关系修订。
        source: 最近一次写入的来源。
    """

    alias: str
    instance_id: str
    members: tuple[str, ...]
    content: tuple[str, ...]
    roots: tuple[str, ...]
    axes: dict[str, dict[str, bool]]
    revision: int
    source: str


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
        self._startup_context: ContextVar[tuple[str, str, asyncio.Task[Any] | None] | None] = (
            ContextVar("ability_startup", default=None)
        )
        self.read_limit = 65536
        self._version_availability: Callable[[AbilityVersion], bool] | None = None

    def set_version_availability(self, check: Callable[[AbilityVersion], bool]) -> None:
        """由受信宿主注入来源可用性；只派生投影，不改写授权事实。"""
        self._version_availability = check

    def _version_available(self, row: Any) -> bool:
        if self._version_availability is None:
            return True
        return self._version_availability(
            AbilityVersion(row["digest"], row["definition"], row["implementation"], row["manifest"])
        )

    @classmethod
    async def open(
        cls,
        path: str | Path,
        project_id: str,
        *,
        operators: frozenset[str],
        runs: Callable[[str], str | None],
        read_limit: int = 65536,
    ) -> AbilityAccessStore:
        """显式初始化项目表；不修改既有上下文 schema 或启动 Agent。"""
        if type(read_limit) is not int or not 1 <= read_limit <= 65536:
            raise ValueError("内容单次读取上限必须在 1..65536 内")
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
                if not exists:
                    async with db.execute(
                        "SELECT name FROM sqlite_master WHERE type='table' "
                        "AND name GLOB 'ability_*'"
                    ) as cursor:
                        if await cursor.fetchone() is not None:
                            raise ValueError(
                                "Ability 域缺少版本标记；须显式重建，禁止继承旧名称授权"
                            )
                if exists:
                    async with db.execute("SELECT version FROM ability_tree_schema") as cursor:
                        version = await cursor.fetchone()
                    if version is None or version[0] != 5:
                        raise ValueError(
                            "Ability 关系数据库使用旧主体语义；稳定 Agent ID 需要显式重建 "
                            "Ability 域（scripts/reset_ability_grants.py），不能按名称继承授权"
                        )
                for statement in _SCHEMA.split(";"):
                    if statement.strip():
                        await db.execute(statement)
                await db.commit()
            except BaseException:
                await db.rollback()
                raise
            store = cls(db, project_id, operators, runs)
            store.read_limit = read_limit
            return store
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
            row = await cursor.fetchone()
        if table == "ability_versions" and row is not None:
            version = AbilityVersion(
                row["digest"], row["definition"], row["implementation"], row["manifest"]
            )
            if version.expected_digest() != version.digest:
                raise AccessError("unavailable")
        return row

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
        """核对目标 Agent 行；操作员不依赖运行实例身份，Agent 自身必须匹配当前 run。"""
        if actor.project_id != self.project_id:
            raise AccessError("access_denied")
        row = await self._row("ability_agents", "id=?", agent)
        if row is None:
            raise AccessError("not_found")
        if self._operator(actor):
            return row
        run = self.current_run(agent)
        if actor.operator or actor.subject_id != agent or actor.run_id != run:
            raise AccessError("access_denied")
        return row

    async def _bump(
        self,
        actor: AccessIdentity,
        agent: str,
        expected: int,
        reason: str,
        version_digest: str | None = None,
        policy: str | None = None,
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
            "before_revision,version_digest,source,policy) VALUES(?,?,?,?,?,?,?,?,?)",
            (
                self.project_id,
                agent,
                revision,
                reason,
                json.dumps({"subject": actor.subject_id, "run": actor.run_id}),
                expected,
                version_digest,
                actor.source,
                policy,
            ),
        )
        owner = self._startup_context.get()
        if owner is not None and owner[0] == agent and owner[2] is asyncio.current_task():
            await self._db.execute(
                "UPDATE ability_registrations SET owned_revision=? "
                "WHERE project=? AND agent=? AND token=? AND state='pending'",
                (revision, self.project_id, agent, owner[1]),
            )
        return revision

    @asynccontextmanager
    async def startup_registration(
        self,
        actor: AccessIdentity,
        agent: str,
    ) -> AsyncIterator[StartupRegistration]:
        """库级独占稳定主体；失败仅回滚本次拥有的修订，不覆盖并发管理写入。

        占位只记录注册所有者，不复制 run。进程异常退出留下的占位必须停机核对
        后显式处理，不以超时自动夺取一个可能仍在运行的主体。
        """
        self._require_operator(actor)
        token = uuid4().hex
        snapshots: dict[str, list[dict[str, Any]]] = {}
        async with self._tx(write=True):
            if await self._row("ability_registrations", "agent=?", agent):
                raise AccessError("conflict")
            for table, key in (
                ("ability_agents", "id"),
                ("ability_grants", "agent"),
                ("ability_tools", "agent"),
            ):
                async with self._db.execute(
                    f"SELECT * FROM {table} WHERE project=? AND {key}=?", (self.project_id, agent)
                ) as cursor:
                    snapshots[table] = [dict(row) for row in await cursor.fetchall()]
            old = snapshots["ability_agents"]
            revision = old[0]["revision"] if old else 0
            async with self._db.execute(
                "SELECT COALESCE(MAX(seq),0) FROM ability_changes WHERE project=? AND agent=?",
                (self.project_id, agent),
            ) as cursor:
                sequence = (await cursor.fetchone())[0]
            await self._db.execute(
                "INSERT INTO ability_registrations VALUES(?,?,?,?,?)",
                (self.project_id, agent, token, "pending", revision),
            )
        registration = StartupRegistration()
        context = self._startup_context.set((agent, token, asyncio.current_task()))
        try:
            yield registration
        finally:
            self._startup_context.reset(context)
            async with self._tx(write=True):
                claim = await self._row(
                    "ability_registrations", "agent=? AND token=?", agent, token
                )
                if claim is None:
                    raise AccessError("conflict")
                if registration.committed:
                    await self._db.execute(
                        "UPDATE ability_registrations SET state='active' "
                        "WHERE project=? AND agent=?",
                        (self.project_id, agent),
                    )
                else:
                    row = await self._row("ability_agents", "id=?", agent)
                    current = row["revision"] if row else 0
                    if current != claim["owned_revision"]:
                        # 不覆盖另一任务的授权变更；保留占位阻止残留被当作可恢复授权。
                        raise AccessError("conflict")
                    for table, key in (
                        ("ability_tools", "agent"),
                        ("ability_grants", "agent"),
                        ("ability_agents", "id"),
                    ):
                        await self._db.execute(
                            f"DELETE FROM {table} WHERE project=? AND {key}=?",
                            (self.project_id, agent),
                        )
                    for table in ("ability_agents", "ability_grants", "ability_tools"):
                        for saved in snapshots[table]:
                            columns = ",".join(saved)
                            slots = ",".join("?" for _ in saved)
                            await self._db.execute(
                                f"INSERT INTO {table}({columns}) VALUES({slots})",
                                tuple(saved.values()),
                            )
                    await self._db.execute(
                        "DELETE FROM ability_changes WHERE project=? AND agent=? AND seq>?",
                        (self.project_id, agent, sequence),
                    )
                    await self._db.execute(
                        "DELETE FROM ability_registrations WHERE project=? AND agent=? AND token=?",
                        (self.project_id, agent, token),
                    )

    async def release_registration(self, actor: AccessIdentity, agent: str) -> None:
        """宿主已停止该主体后解除独占注册，不撤销持久授权。"""
        self._require_operator(actor)
        async with self._tx(write=True):
            await self._db.execute(
                "DELETE FROM ability_registrations WHERE project=? AND agent=? AND state='active'",
                (self.project_id, agent),
            )

    async def commit_version(self, actor: AccessIdentity, version: AbilityVersion) -> None:
        """提交确定定义；相同版本幂等，不扫描来源或自动给 Agent 授权。"""
        self._require_operator(actor)
        if version.expected_digest() != version.digest:
            raise AccessError("invalid_change")
        definition = version.definition()
        manifest = json.loads(version.manifest_json)
        expected_members = set()
        for key, skill in definition.skills.items():
            expected_members.add(f"skill/{key}")
            for role, resources in (
                ("assets", skill.assets),
                ("references", skill.references),
                ("scripts", getattr(skill, "scripts", {})),
            ):
                if not isinstance(resources, dict):
                    if resources:
                        raise AccessError("invalid_change")
                    continue
                expected_members.update(f"skill/{key}/{role}/{item}" for item in resources)
        if set(manifest) != expected_members:
            raise AccessError("invalid_change")
        async with self._tx(write=True):
            for digest, data in version.blobs:
                if digest_bytes(data) != digest:
                    raise AccessError("invalid_change")
                await self._db.execute(
                    "INSERT OR IGNORE INTO ability_content VALUES(?,?,?)",
                    (self.project_id, digest, data),
                )
            for entry in manifest.values():
                blob = await self._row("ability_content", "digest=?", entry["digest"])
                if (
                    blob is None
                    or digest_bytes(blob["data"]) != entry["digest"]
                    or len(blob["data"]) != entry["size"]
                ):
                    raise AccessError("invalid_change")
            await self._db.execute(
                "INSERT OR IGNORE INTO ability_versions VALUES(?,?,?,?,?)",
                (
                    self.project_id,
                    version.digest,
                    version.definition_json,
                    version.implementation_digest,
                    version.manifest_json,
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

    async def ensure_agent(self, actor: AccessIdentity, agent: str) -> int:
        """部署装配在运行实例出现前登记 Agent 行，返回当前关系修订。

        启动核定发生在 Agent 注册之前：授权事实是 Agent 级持久事实，不依赖
        运行实例已经存在；真正绑定 run 的核对仍由每次读取时的 ``_agent`` 完成。

        Args:
            actor: 操作员身份。
            agent: Agent 标识。

        Returns:
            当前关系修订（新行从 0 开始）。

        Raises:
            AccessError: 调用方不是操作员。
        """
        self._require_operator(actor)
        TypeAdapter(TreeId).validate_python(agent)
        async with self._tx(write=True):
            row = await self._row("ability_agents", "id=?", agent)
            if row is None:
                await self._db.execute(
                    "INSERT INTO ability_agents VALUES(?,?,0)", (self.project_id, agent)
                )
                return 0
            return int(row["revision"])

    async def agent_revision(self, actor: AccessIdentity, agent: str) -> int:
        """读取 Agent 关系修订；操作员专用，不要求运行实例已存在。

        Args:
            actor: 操作员身份。
            agent: Agent 标识。

        Returns:
            当前关系修订。

        Raises:
            AccessError: 调用方不是操作员，或 Agent 行不存在。
        """
        self._require_operator(actor)
        async with self._tx():
            row = await self._row("ability_agents", "id=?", agent)
            if row is None:
                raise AccessError("not_found")
            return int(row["revision"])

    async def migrated(self, actor: AccessIdentity, agent: str) -> bool:
        """该 Agent 是否已完成一次性迁移；迁移幂等标记，不随撤销消失。

        标记读的是变更历史里的 ``source='migration'`` 行，而不是当前获授行：
        操作员撤销迁移写入的授权后标记仍在，迁移不会被重放补回。

        Args:
            actor: 操作员身份。
            agent: Agent 标识。

        Returns:
            ``True`` = 已有迁移历史。

        Raises:
            AccessError: 调用方不是操作员。
        """
        self._require_operator(actor)
        async with self._tx():
            async with self._db.execute(
                "SELECT 1 FROM ability_changes WHERE project=? AND agent=? AND source=? LIMIT 1",
                (self.project_id, agent, MIGRATION_SOURCE),
            ) as cursor:
                return await cursor.fetchone() is not None

    async def tool_names(self, actor: AccessIdentity, agent: str) -> tuple[str, ...]:
        """读取已选工具名；操作员专用，启动核定用它清理陈旧选择。

        Args:
            actor: 操作员身份。
            agent: Agent 标识。

        Returns:
            已写入的工具名，按名称排序。
        """
        self._require_operator(actor)
        async with self._tx():
            async with self._db.execute(
                "SELECT name FROM ability_tools WHERE project=? AND agent=? ORDER BY name",
                (self.project_id, agent),
            ) as cursor:
                return tuple(row[0] for row in await cursor.fetchall())

    async def grants(self, actor: AccessIdentity, agent: str) -> list[GrantRecord]:
        """读取 Agent 的全部获授事实快照；不要求运行实例已存在。

        启动核定器需要比较“当前事实”与“目标事实”，因此这里返回原始成员集合
        与轴，而不是经过可见性过滤的投影。

        Args:
            actor: 操作员身份。
            agent: Agent 标识。

        Returns:
            按 alias 排序的获授事实。

        Raises:
            AccessError: 调用方不是操作员，或 Agent 行不存在。
        """
        self._require_operator(actor)
        async with self._tx():
            row = await self._row("ability_agents", "id=?", agent)
            if row is None:
                raise AccessError("not_found")
            async with self._db.execute(
                "SELECT * FROM ability_grants WHERE project=? AND agent=? ORDER BY alias",
                (self.project_id, agent),
            ) as cursor:
                rows = await cursor.fetchall()
            return [
                GrantRecord(
                    alias=item["alias"],
                    instance_id=item["instance"],
                    members=tuple(json.loads(item["members"])),
                    content=tuple(json.loads(item["content"])),
                    roots=tuple(json.loads(item["roots"])),
                    axes=json.loads(item["axes"]),
                    revision=int(item["revision"]),
                    source=item["source"],
                )
                for item in rows
            ]

    async def select_instance(
        self,
        actor: AccessIdentity,
        instance: str,
        digest: str,
        root: Path | None = None,
        *,
        expected_revision: int = 0,
    ) -> int:
        """显式切换实例版本；实际变化撤去旧获授关联，禁止旧许可自动扩大。"""
        self._require_operator(actor)
        TypeAdapter(TreeId).validate_python(instance)
        resolved = str(root.resolve()) if root is not None else ""
        async with self._tx(write=True):
            version = await self._row("ability_versions", "digest=?", digest)
            if version is None:
                raise AccessError("not_found")
            if not self._version_available(version):
                raise AccessError("unavailable")
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
                "manifest": json.loads(version["manifest"]),
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
        """写入一次获授变更并返回投影。

        写入与投影分两个事务：写入只要求关系的当前修订，投影才要求运行实例
        身份。启动核定在 Agent 注册前写入授权，因此只调用 :meth:`write_grant`。
        """
        await self.write_grant(actor, agent, change)
        return await self._view(agent)

    async def write_grant(
        self, actor: AccessIdentity, agent: str, change: GrantAbilityRequest
    ) -> int:
        """写入一次获授变更并返回关系修订；相同事实不产生新历史。

        授予集合是**授权时刻的快照**：整包授权展开为当时的 Operation、
        skill 正文与附件、以及脚本可见性；脚本执行不在其中，由 ``axes``
        显式给出。成员的可见性与可用性一并物化，读取端不再推导。

        Args:
            actor: 操作员身份；写入来源取 ``actor.source``。
            agent: Agent 标识。
            change: 获授变更请求。

        Returns:
            写入后的关系修订；无实际变化时返回当前修订。

        Raises:
            AccessError: 冲突、实例缺失或授予内容非法。
        """
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
            try:
                facts = request_facts(definition, change)
            except ValueError as error:
                raise AccessError("invalid_change") from error
            if any(not Path(root).is_absolute() for root in change.allowed_roots):
                raise AccessError("invalid_change")
            roots = sorted({str(Path(root).resolve()) for root in change.allowed_roots})
            members = sorted(facts.members)
            content = sorted(facts.content)
            axes = facts.normalized_axes()
            manifest = json.loads(version["manifest"])
            operations = {key for key, item in definition["operations"].items() if item["expose"]}
            if (
                not set(members) <= operations
                or not set(content) <= set(manifest)
                or not (members or content)
                or (
                    any(file_member_paths(definition, member) for member in members)
                    and (not roots or not instance["root"])
                )
                or not all(Path(root).is_relative_to(instance["root"]) for root in roots)
            ):
                raise AccessError("invalid_change")
            previous = await self._row("ability_grants", "agent=? AND alias=?", agent, change.alias)
            if previous and (
                previous["instance"] == change.instance_id
                and json.loads(previous["members"]) == members
                and json.loads(previous["content"]) == content
                and json.loads(previous["roots"]) == roots
                and json.loads(previous["axes"]) == axes
            ):
                return current["revision"]
            revision = await self._bump(
                actor,
                agent,
                change.expected_revision,
                "grant",
                instance["digest"],
                change.policy,
            )
            await self._remove_grant(agent, change.alias)
            await self._db.execute(
                "INSERT INTO ability_grants VALUES(?,?,?,?,?,?,?,?,?,?)",
                (
                    self.project_id,
                    agent,
                    change.alias,
                    change.instance_id,
                    json.dumps(members),
                    json.dumps(roots),
                    revision,
                    json.dumps(content),
                    json.dumps(axes),
                    actor.source,
                ),
            )
            return revision

    async def revoke(
        self, actor: AccessIdentity, agent: str, change: RevokeAbilityRequest
    ) -> AbilityTreeView:
        """撤销获授及其工具选择，不停止共享实例。"""
        await self.write_revoke(actor, agent, change)
        return await self._view(agent)

    async def write_revoke(
        self, actor: AccessIdentity, agent: str, change: RevokeAbilityRequest
    ) -> int:
        """写入一次撤销并返回关系修订；不读取需要运行实例身份的投影。

        Args:
            actor: 操作员身份。
            agent: Agent 标识。
            change: 撤销请求；``members`` 为 None 时整条获授关系撤销。

        Returns:
            写入后的关系修订；无实际变化时返回当前修订。

        Raises:
            AccessError: 冲突或撤销目标不存在。
        """
        self._require_operator(actor)
        async with self._tx(write=True):
            current = await self._agent(actor, agent)
            if current["revision"] != change.expected_revision:
                raise AccessError("conflict")
            grant = await self._row("ability_grants", "agent=? AND alias=?", agent, change.alias)
            if grant is None:
                return current["revision"]
            facts = stored_facts(
                json.loads(grant["members"]),
                json.loads(grant["content"]),
                json.loads(grant["axes"]),
            )
            if change.members is not None:
                valid = facts.addresses()
                for target in change.members:
                    if target not in valid and not any(
                        item.startswith(target + "/") for item in facts.content
                    ):
                        raise AccessError("invalid_change")
                for target in change.members:
                    facts = facts.without_address(target)
            revision = await self._bump(actor, agent, change.expected_revision, "revoke")
            if change.members is None or facts.empty:
                await self._remove_grant(agent, change.alias)
            else:
                await self._db.execute(
                    "UPDATE ability_grants SET members=?,content=?,axes=?,revision=? "
                    "WHERE project=? AND agent=? AND alias=?",
                    (
                        json.dumps(sorted(facts.members)),
                        json.dumps(sorted(facts.content)),
                        json.dumps(facts.normalized_axes()),
                        revision,
                        self.project_id,
                        agent,
                        change.alias,
                    ),
                )
                async with self._db.execute(
                    "SELECT name,member FROM ability_tools WHERE project=? AND agent=? AND alias=?",
                    (self.project_id, agent, change.alias),
                ) as cursor:
                    tools = await cursor.fetchall()
                for tool in tools:
                    if tool["member"] not in facts.members:
                        await self._db.execute(
                            "DELETE FROM ability_tools WHERE project=? AND agent=? AND name=?",
                            (self.project_id, agent, tool["name"]),
                        )
                    else:
                        await self._db.execute(
                            "UPDATE ability_tools SET grant_revision=? "
                            "WHERE project=? AND agent=? AND name=?",
                            (revision, self.project_id, agent, tool["name"]),
                        )
            return revision

    async def bind_tool(
        self, actor: AccessIdentity, agent: str, change: BindToolRequest
    ) -> AbilityTreeView:
        """只从本 Agent 当前获授集合选择工具，不能增加授权。"""
        await self.write_bind_tool(actor, agent, change)
        return await self._view(agent)

    async def write_bind_tool(
        self, actor: AccessIdentity, agent: str, change: BindToolRequest
    ) -> int:
        """写入一次工具选择并返回关系修订；相同选择不产生新历史。

        Args:
            actor: 受信调用方身份；Agent 自身或操作员均可。
            agent: Agent 标识。
            change: 工具选择请求。

        Returns:
            写入后的关系修订；无实际变化时返回当前修订。

        Raises:
            AccessError: 冲突、未获授或成员不可暴露。
        """
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
                return current["revision"]
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
            return revision

    async def unbind_tool(
        self, actor: AccessIdentity, agent: str, change: UnbindToolRequest
    ) -> AbilityTreeView:
        """删除工具选择，保留获授关系。"""
        await self.write_unbind_tool(actor, agent, change)
        return await self._view(agent)

    async def write_unbind_tool(
        self, actor: AccessIdentity, agent: str, change: UnbindToolRequest
    ) -> int:
        """写入一次工具取消并返回关系修订；不存在的选择按无变化处理。

        Args:
            actor: 受信调用方身份。
            agent: Agent 标识。
            change: 工具取消请求。

        Returns:
            写入后的关系修订；无实际变化时返回当前修订。

        Raises:
            AccessError: 冲突。
        """
        async with self._tx(write=True):
            current = await self._agent(actor, agent)
            if current["revision"] != change.expected_revision:
                raise AccessError("conflict")
            if (
                await self._row("ability_tools", "agent=? AND name=?", agent, change.tool_name)
                is None
            ):
                return current["revision"]
            revision = await self._bump(actor, agent, change.expected_revision, "unbind_tool")
            await self._db.execute(
                "DELETE FROM ability_tools WHERE project=? AND agent=? AND name=?",
                (self.project_id, agent, change.tool_name),
            )
            return revision

    async def _view(self, agent: str) -> AbilityTreeView:
        row = await self._row("ability_agents", "id=?", agent)
        abilities = []
        available_aliases: set[str] = set()
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
            content = json.loads(grant["content"])
            axes = json.loads(grant["axes"])
            available = self._version_available(version)
            if available:
                available_aliases.add(grant["alias"])
            if not available:
                axes = {path: {**fact, "available": False} for path, fact in axes.items()}
            skills = self._skill_nodes(definition, json.loads(version["manifest"]), content, axes)
            if not available:
                skills = [skill.model_copy(update={"body_allowed": False}) for skill in skills]
            abilities.append(
                AbilityNode(
                    alias=grant["alias"],
                    instance_id=instance["id"],
                    instance_revision=instance["revision"],
                    definition_digest="sha256:"
                    + hashlib.sha256(version["definition"].encode()).hexdigest(),
                    version_digest=instance["digest"],
                    revision=grant["revision"],
                    availability="unknown" if available else "unavailable",
                    allowed_roots=json.loads(grant["roots"]),
                    skills=skills,
                    skill=skills[0] if skills else None,
                    operations=[
                        OperationNode(
                            key=key,
                            description=definition["operations"][key]["description"],
                            input_schema=definition["operations"][key]["input_schema"],
                            output_schema=operation_result_schema(
                                definition["operations"][key]["output_schema"]
                            ),
                            availability=(
                                "unavailable"
                                if not available
                                or not axes.get(f"operations/{key}", {}).get("available", True)
                                else "unknown"
                            ),
                        )
                        for key in members
                        if axes.get(f"operations/{key}", {}).get("visible", True)
                        and definition["operations"][key]["expose"]
                    ],
                )
            )
        async with self._db.execute(
            "SELECT * FROM ability_tools WHERE project=? AND agent=? ORDER BY name",
            (self.project_id, agent),
        ) as cursor:
            tools = await cursor.fetchall()
        visible = {
            node.alias: {item.key for item in node.operations}
            for node in abilities
            if node.alias in available_aliases
        }
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

    @staticmethod
    def _skill_nodes(
        definition: dict[str, Any],
        manifest: dict[str, Any],
        content: list[str],
        axes: dict[str, dict[str, bool]] | None = None,
    ) -> list[SkillNode]:
        """按内容清单投影全部获授技能（一个包可以有多个技能）。

        Args:
            definition: 版本的归一化定义。
            manifest: 版本的内容清单。
            content: 本次授予覆盖的成员地址。
            axes: 成员的可见性与可用性事实；缺省时按“可见、脚本不可执行”
                推断，只供样例导出等不带关系库的调用方使用。

        Returns:
            按定义面键序排列的技能节点；没有任何获授内容时为空。
        """
        if not content:
            return []
        facts = axes or {}
        nodes: list[SkillNode] = []
        for key, skill in definition["skills"].items():
            prefix = f"skill/{key}"
            related = [path for path in content if path == prefix or path.startswith(f"{prefix}/")]
            if not related:
                continue

            def available(path: str) -> bool:
                fact = facts.get(path)
                if fact is not None:
                    return bool(fact.get("available", True))
                return "/scripts/" not in path

            def children(role: str, prefix: str = prefix) -> list[ContentResourceNode]:
                return [
                    ContentResourceNode(
                        key=path.rsplit("/", 1)[-1],
                        member=path,
                        available=available(path),
                        **manifest[path],
                    )
                    for path in related
                    if path.startswith(f"{prefix}/{role}/")
                ]

            nodes.append(
                SkillNode(
                    key=key,
                    title=skill["title"],
                    description=skill["description"],
                    body_allowed=prefix in content,
                    assets=children("assets"),
                    references=children("references"),
                    scripts=children("scripts"),
                )
            )
        return nodes

    async def view(self, actor: AccessIdentity, agent: str) -> AbilityTreeView:
        """读取提交事实与宿主提供的可用性投影；不更新授权状态。"""
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
            return await self._resolve(
                actor, alias, member, tool=tool, global_instance=global_instance
            )

    async def resolve_script(
        self, actor: AccessIdentity, agent: str, grant_address: str
    ) -> ScriptGrant:
        """按当前 run 解析一个已可见的脚本授予。

        内容成员的 ``admit`` 只核可读，不核脚本执行权。这里单独看
        ``skill/<skill>/scripts/<key>`` 的可见性与 ``available``：可见但
        ``available`` 为假时拒绝执行，未出现在可见授予里时按不存在处理。
        操作员身份不能代替 Agent 自己的 run。

        Args:
            actor: Agent 自己的受信身份，``subject_id`` 与 ``run_id`` 必须是当前 run。
            agent: Agent 标识，须与 ``actor.subject_id`` 相同。
            grant_address: 授予地址，不是磁盘路径，也不是模型看到的 ``skills/`` 地址。

        Returns:
            关系库能确定的脚本授予。调用方还要在起进程前核对包目录与文件。

        Raises:
            AccessError: ``not_found`` 没有可见脚本；``conflict`` 多个包撞同一个地址；
                ``unavailable`` 版本已不可用；``access_denied`` 可见但未授执行权，
                或身份/run 不匹配。
        """
        if _SCRIPT_GRANT_ADDRESS.fullmatch(grant_address) is None:
            raise AccessError("not_found")
        async with self._tx():
            await self._agent(actor, agent)
            if actor.subject_id != agent or actor.run_id != self.current_run(agent):
                raise AccessError("access_denied")
            async with self._db.execute(
                "SELECT * FROM ability_grants WHERE project=? AND agent=? ORDER BY alias",
                (self.project_id, agent),
            ) as cursor:
                grants = await cursor.fetchall()
            matches: list[tuple[Any, Any, Any]] = []
            for grant in grants:
                content = json.loads(grant["content"])
                if grant_address not in content:
                    continue
                axes = json.loads(grant["axes"])
                fact = axes.get(grant_address) or {}
                if not fact.get("visible", True):
                    continue
                instance = await self._row("ability_instances", "id=?", grant["instance"])
                if instance is None:
                    continue
                version = await self._row("ability_versions", "digest=?", instance["digest"])
                if version is None:
                    continue
                matches.append((grant, instance, version))
            if not matches:
                raise AccessError("not_found")
            if len(matches) > 1:
                raise AccessError("conflict")
            grant, instance, version = matches[0]
            if not self._version_available(version):
                raise AccessError("unavailable")
            fact = json.loads(grant["axes"]).get(grant_address) or {}
            if not fact.get("available", False):
                raise AccessError("access_denied")
            manifest = json.loads(version["manifest"])
            entry = manifest.get(grant_address)
            if not isinstance(entry, dict):
                raise AccessError("not_found")
            relative = entry.get("path")
            digest = entry.get("digest")
            size = entry.get("size")
            if (
                not isinstance(relative, str)
                or not isinstance(digest, str)
                or type(size) is not int
            ):
                raise AccessError("unavailable")
            definition = AbilityVersion(
                version["digest"],
                version["definition"],
                version["implementation"],
                version["manifest"],
            ).definition()
            identity = definition.identity
            return ScriptGrant(
                alias=grant["alias"],
                instance_id=instance["id"],
                version_digest=instance["digest"],
                grant_address=grant_address,
                workspace=instance["root"],
                package=identity.full_name,
                release=identity.version,
                container=definition.container_name,
                namespace=identity.namespace,
                relative_path=relative,
                digest=digest,
                size=size,
            )

    async def _resolve(
        self,
        actor: AccessIdentity,
        alias: str,
        member: str,
        *,
        tool: str | None,
        global_instance: bool = False,
    ) -> MemberFence:
        """解析并核对成员引用；调用方持有事务。"""
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
        fence = replace(fence, version_digest=instance["digest"])
        await self._check(actor, fence)
        return fence

    async def _check(
        self, actor: AccessIdentity, fence: MemberFence, arguments: dict[str, Any] | None = None
    ) -> str:
        instance = await self._row("ability_instances", "id=?", fence.instance_id)
        if instance is None or instance["revision"] != fence.instance_revision:
            raise AccessError("access_denied")
        version = await self._row("ability_versions", "digest=?", instance["digest"])
        if fence.version_digest and fence.version_digest != instance["digest"]:
            raise AccessError("access_denied")
        definition = json.loads(version["definition"])
        if not self._version_available(version):
            raise AccessError("unavailable")
        is_content = fence.member.startswith("skill/")
        if is_content:
            if fence.member not in json.loads(version["manifest"]):
                raise AccessError("access_denied")
            if fence.tool_name != "_":
                raise AccessError("access_denied")
        else:
            operation = definition["operations"].get(fence.member)
            if operation is None or not operation["expose"]:
                raise AccessError("access_denied")
        roots: tuple[str, ...] = (instance["root"],)
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
                or fence.member
                not in json.loads(grant["content"] if is_content else grant["members"])
            ):
                raise AccessError("access_denied")
            roots = tuple(json.loads(grant["roots"]))
            axes = json.loads(grant["axes"])
            if not is_content:
                fact = axes.get(f"operations/{fence.member}", {})
                if not fact.get("visible", True):
                    raise AccessError("access_denied")
                if not fact.get("available", True):
                    raise AccessError("unavailable")
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
            admission = admission_for(definition, fence.member)
            if admission is None:
                raise AccessError("unavailable")
            if admission.contract == PYTHON_CONTRACT and self._version_availability is None:
                raise AccessError("unavailable")
            admission.check(fence.member, arguments, roots, instance["root"])
        return f"{instance['digest']}:{fence.grant_revision}:{fence.tool_revision}"

    async def authorize(self, actor: AccessIdentity, fence: MemberFence) -> str:
        """核对当前关系，不签发另一份许可。"""
        async with self._tx():
            return await self._check(actor, fence)

    async def admit(
        self, actor: AccessIdentity, fence: MemberFence, arguments: dict[str, Any]
    ) -> tuple[str, None]:
        """只读成员在读取事务中核定，不抢写锁或持久化调用参数。"""
        async with self._tx():
            return await self._admit(actor, fence, arguments), None

    async def admit_tool(
        self,
        actor: AccessIdentity,
        agent: str,
        tool_name: str,
        arguments: dict[str, Any],
        *,
        expected_alias: str | None = None,
        expected_member: str | None = None,
    ) -> str:
        """按 Agent 当前工具选择解析成员并在同一事务内完成接纳。

        生产执行入口只拿得到模型传来的工具名，因此必须把**启动时固定的**
        成员身份作为期望值传回：若当前选择已把同名工具改绑到别的成员，
        说明调用依据与执行实现不再一致，直接拒绝，不能按新绑定放行旧实现。

        Args:
            actor: 受信调用方身份（Agent 自身）。
            agent: Agent 标识。
            tool_name: 当前工具选择里的工具名。
            arguments: 本次调用的参数。
            expected_alias: 启动时固定的能力别名；给出时当前选择必须一致。
            expected_member: 启动时固定的成员键；给出时当前选择必须一致。

        Returns:
            接纳依据（版本摘要 + 关系修订 + 工具修订）。

        Raises:
            AccessError: 工具未选择、绑定已改、关系失效或参数越界。
        """
        async with self._tx():
            tool = await self._row("ability_tools", "agent=? AND name=?", agent, tool_name)
            if tool is None:
                raise AccessError("access_denied")
            if expected_alias is not None and tool["alias"] != expected_alias:
                raise AccessError("access_denied")
            if expected_member is not None and tool["member"] != expected_member:
                raise AccessError("access_denied")
            fence = await self._resolve(actor, tool["alias"], tool["member"], tool=tool_name)
            return await self._admit(actor, fence, arguments)

    async def _admit(
        self, actor: AccessIdentity, fence: MemberFence, arguments: dict[str, Any]
    ) -> str:
        """接纳核心：内容成员校验读取请求，其余成员走关系校验 + 后端参数检查。"""
        if fence.member.startswith("skill/"):
            ContentReadRequest.model_validate(arguments)
            return await self._check(actor, fence)
        return await self._check(actor, fence, arguments)

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
                "SELECT digest,definition,implementation,manifest FROM ability_versions "
                "WHERE project=? ORDER BY digest",
                (self.project_id,),
            ) as cursor:
                return [AbilityVersion(*row) for row in await cursor.fetchall()]

    async def describe_content(
        self, actor: AccessIdentity, fence: MemberFence
    ) -> ContentDescription:
        """读取有权限的确定资源描述，不包含正文或文件系统位置。"""
        if not fence.member.startswith("skill/"):
            raise AccessError("access_denied")
        async with self._tx():
            await self._check(actor, fence)
            instance = await self._row("ability_instances", "id=?", fence.instance_id)
            version = await self._row("ability_versions", "digest=?", instance["digest"])
            entry = json.loads(version["manifest"])[fence.member]
            return ContentDescription(
                instance_id=fence.instance_id,
                version_digest=instance["digest"],
                member=fence.member,
                path=entry["path"],
                media_type=entry["media_type"],
                digest=entry["digest"],
                total_bytes=entry["size"],
            )

    async def read_content(
        self, actor: AccessIdentity, fence: MemberFence, request: ContentReadRequest
    ) -> ContentReadResult:
        """为 JSON 消费方转换领域字节块，保持相同接纳边界。"""
        return (await self.read_chunk(actor, fence, request)).to_json_result()

    async def read_chunk(
        self, actor: AccessIdentity, fence: MemberFence, request: ContentReadRequest
    ) -> ContentChunk:
        """在只读事务中最后核权并读取固定字节；不获取写锁。"""
        if not fence.member.startswith("skill/"):
            raise AccessError("access_denied")
        if request.limit > self.read_limit:
            raise AccessError("invalid_change")
        async with self._tx():
            await self._check(actor, fence)
            instance = await self._row("ability_instances", "id=?", fence.instance_id)
            version = await self._row("ability_versions", "digest=?", instance["digest"])
            entry = json.loads(version["manifest"])[fence.member]
            blob = await self._row("ability_content", "digest=?", entry["digest"])
            if (
                blob is None
                or digest_bytes(blob["data"]) != entry["digest"]
                or len(blob["data"]) != entry["size"]
            ):
                raise AccessError("unavailable")
            if request.offset > entry["size"]:
                raise AccessError("invalid_change")
            data = blob["data"][request.offset : request.offset + request.limit]
            end = request.offset + len(data)
            return ContentChunk(
                description=ContentDescription(
                    instance_id=fence.instance_id,
                    version_digest=instance["digest"],
                    member=fence.member,
                    path=entry["path"],
                    media_type=entry["media_type"],
                    digest=entry["digest"],
                    total_bytes=entry["size"],
                ),
                offset=request.offset,
                data=data,
                next_offset=end if end < entry["size"] else None,
            )

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
