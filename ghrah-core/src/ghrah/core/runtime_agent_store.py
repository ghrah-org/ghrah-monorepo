"""仅显式 Agent API 使用的 Core 定义存储；不保存运行或投递状态副本。"""

import json
import re
from datetime import UTC, datetime
from typing import Any

import aiosqlite

_SCHEMA = """
CREATE TABLE IF NOT EXISTS runtime_agents (
    agent_id TEXT PRIMARY KEY,
    scope_ref TEXT NOT NULL,
    external_ref TEXT NOT NULL,
    config TEXT NOT NULL,
    assembly_digest TEXT NOT NULL,
    created_at TEXT NOT NULL,
    UNIQUE(scope_ref, external_ref)
);
CREATE TABLE IF NOT EXISTS runtime_create_receipts (
    scope_ref TEXT NOT NULL,
    idempotency_key TEXT NOT NULL,
    request_digest TEXT NOT NULL,
    body TEXT NOT NULL,
    headers TEXT NOT NULL,
    PRIMARY KEY(scope_ref, idempotency_key)
);
"""


def runtime_now(timestamp: float | None = None) -> str:
    """返回 UTC 毫秒时间；持久消息时间按同一格式表示。"""
    value = datetime.now(UTC) if timestamp is None else datetime.fromtimestamp(timestamp, UTC)
    return value.isoformat(timespec="milliseconds").replace("+00:00", "Z")


class RuntimeAgentStore:
    """数据库 owner 已由 RuntimeResources 获取，本连接只拥有新增定义表。"""

    def __init__(self, database_path: str, scope_ref: str) -> None:
        """保存显式数据库和作用域，不执行 IO。"""
        self.path = database_path
        self.scope_ref = scope_ref
        self.db: aiosqlite.Connection | None = None

    async def open(self) -> None:
        """准备定义表及投递权威表的时间元数据，不启动 Agent。"""
        self.db = await aiosqlite.connect(self.path)
        self.db.row_factory = aiosqlite.Row
        await self.db.execute("PRAGMA foreign_keys=ON")
        try:
            await self.db.executescript(_SCHEMA)
            columns = await (await self.db.execute("PRAGMA table_info(runtime_agents)")).fetchall()
            names = {column[1] for column in columns}
            for name, declaration in (
                ("config_revision", "INTEGER NOT NULL DEFAULT 1"),
                ("updated_at", "TEXT NOT NULL DEFAULT ''"),
                ("deleted_at", "TEXT"),
            ):
                if name not in names:
                    await self.db.execute(f"ALTER TABLE runtime_agents ADD COLUMN {name} {declaration}")
            identities = await (
                await self.db.execute("SELECT agent_id FROM runtime_agents")
            ).fetchall()
            if any(not re.fullmatch(r"[0-9a-f]{32}", row[0]) for row in identities):
                raise ValueError(
                    "Pre-release Runtime identities require an explicit database rebuild"
                )
            columns = await (await self.db.execute("PRAGMA table_info(mm_deliveries)")).fetchall()
            if "updated_at" not in {row[1] for row in columns}:
                await self.db.execute("ALTER TABLE mm_deliveries ADD COLUMN updated_at REAL")
            # 时间属于投递权威表。触发器不写状态或投影表，也不产生第二份运行事实。
            await self.db.executescript("""
                CREATE TRIGGER IF NOT EXISTS runtime_delivery_updated
                AFTER UPDATE OF state, attempts, blocked_reason, last_node_id ON mm_deliveries
                BEGIN
                    UPDATE mm_deliveries
                    SET updated_at=(julianday('now')-2440587.5)*86400.0
                    WHERE delivery_id=NEW.delivery_id;
                END;
            """)
            await self._prepare_node_sequences()
            await self.db.commit()
        except BaseException:
            await self.close()
            raise

    async def _prepare_node_sequences(self) -> None:
        # 只在显式资源入口开启时准备。序号由同一权威库在节点提交事务内分配。
        db = self.connection()
        columns = await (await db.execute("PRAGMA table_info(nodes)")).fetchall()
        if "runtime_node_seq" not in {row[1] for row in columns}:
            await db.execute("ALTER TABLE nodes ADD COLUMN runtime_node_seq INTEGER")
        await db.executescript("""
            CREATE TABLE IF NOT EXISTS runtime_node_counters (
                agent_id TEXT PRIMARY KEY REFERENCES runtime_agents(agent_id),
                high_water INTEGER NOT NULL DEFAULT 0
            );
            CREATE UNIQUE INDEX IF NOT EXISTS idx_runtime_node_seq
            ON nodes(agent_name, runtime_node_seq) WHERE runtime_node_seq IS NOT NULL;
            CREATE INDEX IF NOT EXISTS idx_runtime_node_branch_seq
            ON nodes(agent_name, created_on_branch_id, runtime_node_seq)
            WHERE runtime_node_seq IS NOT NULL;
            CREATE TRIGGER IF NOT EXISTS runtime_allocate_node_seq
            AFTER INSERT ON nodes
            WHEN EXISTS (SELECT 1 FROM runtime_agents WHERE agent_id=NEW.agent_name)
            BEGIN
                INSERT INTO runtime_node_counters(agent_id, high_water)
                VALUES (NEW.agent_name, 1)
                ON CONFLICT(agent_id) DO UPDATE SET high_water=high_water+1;
                UPDATE nodes SET runtime_node_seq=(
                    SELECT high_water FROM runtime_node_counters WHERE agent_id=NEW.agent_name
                ) WHERE id=NEW.id;
            END;
        """)
        # 兼容此前已创建的资源 Agent。回填只在启动迁移执行，不在 GET 时写；
        # 历史排序按既有 iteration/id 明确分配，不宣称等于历史提交时间。
        await db.execute("BEGIN IMMEDIATE")
        rows = await (
            await db.execute(
                "SELECT n.id, n.agent_name FROM nodes n JOIN runtime_agents a "
                "ON a.agent_id=n.agent_name WHERE n.runtime_node_seq IS NULL "
                "ORDER BY n.agent_name, n.iteration, n.id"
            )
        ).fetchall()
        for row in rows:
            await db.execute(
                "INSERT INTO runtime_node_counters(agent_id,high_water) VALUES (?,1) "
                "ON CONFLICT(agent_id) DO UPDATE SET high_water=high_water+1",
                (row["agent_name"],),
            )
            await db.execute(
                "UPDATE nodes SET runtime_node_seq=(SELECT high_water FROM runtime_node_counters "
                "WHERE agent_id=?) WHERE id=?",
                (row["agent_name"], row["id"]),
            )

    async def close(self) -> None:
        """关闭定义连接，数据库所有权由 RuntimeResources 最后释放。"""
        if self.db is not None:
            await self.db.close()
            self.db = None

    def connection(self) -> aiosqlite.Connection:
        """取得已打开的同文件连接。"""
        if self.db is None:
            raise RuntimeError("Agent resource store is not open")
        return self.db

    async def get(self, agent_id: str) -> aiosqlite.Row | None:
        """按受信作用域读取定义。"""
        return await (
            await self.connection().execute(
                "SELECT * FROM runtime_agents WHERE scope_ref=? AND agent_id=? AND deleted_at IS NULL",
                (self.scope_ref, agent_id),
            )
        ).fetchone()

    async def by_external_ref(self, external_ref: str) -> aiosqlite.Row | None:
        """业务键在同一作用域内唯一。"""
        return await (
            await self.connection().execute(
                "SELECT * FROM runtime_agents WHERE scope_ref=? AND external_ref=?",
                (self.scope_ref, external_ref),
            )
        ).fetchone()

    async def receipt(self, key: str) -> aiosqlite.Row | None:
        """读取不可变的创建回执，而非当前资源快照。"""
        return await (
            await self.connection().execute(
                "SELECT * FROM runtime_create_receipts WHERE scope_ref=? AND idempotency_key=?",
                (self.scope_ref, key),
            )
        ).fetchone()

    async def create(
        self,
        record: dict[str, Any],
        *,
        key: str,
        request_digest: str,
        body: dict[str, Any],
        headers: dict[str, str],
    ) -> None:
        """定义和幂等回执在一个 SQLite 事务内持久提交。"""
        db = self.connection()
        try:
            await db.execute("BEGIN IMMEDIATE")
            await db.execute(
                "INSERT INTO runtime_agents(agent_id,scope_ref,external_ref,config,assembly_digest,created_at) VALUES (?, ?, ?, ?, ?, ?)",
                (
                    record["agent_id"],
                    self.scope_ref,
                    record["external_ref"],
                    json.dumps(record["config"], ensure_ascii=False),
                    record["assembly_digest"],
                    record["created_at"],
                ),
            )
            await db.execute(
                "INSERT INTO runtime_create_receipts VALUES (?, ?, ?, ?, ?)",
                (
                    self.scope_ref,
                    key,
                    request_digest,
                    json.dumps(body, ensure_ascii=False),
                    json.dumps(headers),
                ),
            )
            await db.commit()
        except BaseException:
            await db.rollback()
            raise

    async def replace_config(self, agent_id: str, config: dict[str, Any], digest: str, revision: int) -> bool:
        """按资源修订条件写入完整配置；与创建共用唯一权威表。"""
        db = self.connection()
        cursor = await db.execute(
            "UPDATE runtime_agents SET config=?,assembly_digest=?,config_revision=config_revision+1,updated_at=? "
            "WHERE scope_ref=? AND agent_id=? AND config_revision=? AND deleted_at IS NULL",
            (json.dumps(config, ensure_ascii=False), digest, runtime_now(), self.scope_ref, agent_id, revision),
        )
        await db.commit()
        return cursor.rowcount == 1

    async def delete(self, agent_id: str, revision: int) -> bool:
        """保留删除墓碑与创建回执，防止幂等重放复活已删除的身份。"""
        db = self.connection()
        cursor = await db.execute(
            "UPDATE runtime_agents SET deleted_at=?,updated_at=? WHERE scope_ref=? AND agent_id=? "
            "AND config_revision=? AND deleted_at IS NULL",
            (runtime_now(), runtime_now(), self.scope_ref, agent_id, revision),
        )
        await db.commit()
        return cursor.rowcount == 1

    async def page(self, after: str, limit: int) -> list[aiosqlite.Row]:
        """按稳定 ID 排序读摘要页，额外读取一条判断是否有下一页。"""
        return list(
            await (
                await self.connection().execute(
                    "SELECT * FROM runtime_agents WHERE scope_ref=? AND agent_id>? AND deleted_at IS NULL "
                    "ORDER BY agent_id LIMIT ?",
                    (self.scope_ref, after, limit + 1),
                )
            ).fetchall()
        )
