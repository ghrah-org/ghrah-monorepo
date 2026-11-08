# SPDX-FileCopyrightText: 2026 chenxya <chenxya@ghrah.org>
#
# SPDX-License-Identifier: Apache-2.0

"""基于追加式变更记录的 JSON checkpoint 后端。"""

from __future__ import annotations

import gzip
import json
import logging
import os
import shutil
import time
from datetime import UTC, datetime
from pathlib import Path
from typing import Any
from uuid import uuid4

from ghrah.context.persistence.backend import PersistenceBackend
from ghrah.context.persistence.changes import ContextChanges
from ghrah.context.persistence.checkpoint import (
    ContextCheckpoint,
    SnapshotPayloads,
    project_snapshot_payloads,
)
from ghrah.context.persistence.serialization import (
    deserialize_action_session,
    deserialize_branch,
    deserialize_node,
    serialize_action_session,
    serialize_branch,
    serialize_node,
)

logger = logging.getLogger(__name__)

__all__ = ["JsonFileBackend"]

_REPLACE_RETRIES = 5
_REPLACE_RETRY_DELAY_S = 0.05


def _replace_with_retry(src: Path, dst: Path) -> None:
    """原子覆盖文件；Windows 目标短暂锁定时重试。"""
    last_exc: PermissionError | None = None
    for _ in range(_REPLACE_RETRIES):
        try:
            os.replace(src, dst)
            return
        except PermissionError as exc:
            last_exc = exc
            time.sleep(_REPLACE_RETRY_DELAY_S)
    assert last_exc is not None
    raise last_exc


def _generate_run_id() -> str:
    """生成基于当前时间的 run ID。"""
    return f"run_{datetime.now(UTC).strftime('%Y-%m-%dT%H-%M-%S')}"


class JsonFileBackend(PersistenceBackend):
    """将每个 ``ContextChanges`` 保存为一个小型原子记录。

    正常 commit/create/activate 只新增一条记录，不读取或重写历史节点。
    该后端用于本地验证；读取时按文件名顺序重放记录。
    """

    def __init__(
        self,
        root_dir: str | Path | None = None,
        compress: bool = True,
        run_id: str | None = None,
    ) -> None:
        self._root_dir = Path(root_dir) if root_dir is not None else Path.home() / ".ghrah" / "data"
        self._compress = compress
        self._run_id = run_id or _generate_run_id()
        self._run_dir = self._root_dir / self._run_id

    @property
    def root_dir(self) -> Path:
        """存储根目录。"""
        return self._root_dir

    @property
    def run_id(self) -> str:
        """运行 ID。"""
        return self._run_id

    @property
    def run_dir(self) -> Path:
        """本次运行的存储目录。"""
        return self._run_dir

    def _agent_dir(self, agent_name: str) -> Path:
        return self._run_dir / agent_name

    def _changes_dir(self, agent_name: str) -> Path:
        return self._agent_dir(agent_name) / "changes-v3"

    def _record_path(self, agent_name: str) -> Path:
        suffix = ".json.gz" if self._compress else ".json"
        name = f"{time.time_ns():020d}-{uuid4().hex}{suffix}"
        return self._changes_dir(agent_name) / name

    def _write_json(self, path: Path, data: dict[str, Any] | list[Any]) -> None:
        """先写临时文件，再原子发布一条记录。"""
        path.parent.mkdir(parents=True, exist_ok=True)
        tmp_path = path.with_suffix(path.suffix + ".tmp")
        try:
            if path.suffix == ".gz":
                with gzip.open(tmp_path, "wt", encoding="utf-8") as stream:
                    json.dump(data, stream, ensure_ascii=False)
            else:
                with tmp_path.open("w", encoding="utf-8") as stream:
                    json.dump(data, stream, ensure_ascii=False)
            _replace_with_retry(tmp_path, path)
        except Exception:
            logger.exception("Failed to write %s", path)
            tmp_path.unlink(missing_ok=True)
            raise

    @staticmethod
    def _read_json(path: Path) -> dict[str, Any] | list[Any]:
        if path.suffix == ".gz":
            with gzip.open(path, "rt", encoding="utf-8") as stream:
                return json.load(stream)
        with path.open(encoding="utf-8") as stream:
            return json.load(stream)

    @staticmethod
    def _serialize_changes(changes: ContextChanges) -> dict[str, Any]:
        return {
            "schema_version": 3,
            "agent_name": changes.agent_name,
            "active_session_id": changes.active_session_id,
            "sessions": [serialize_action_session(item) for item in changes.sessions],
            "branches": [serialize_branch(item) for item in changes.branches],
            "nodes": [serialize_node(item) for item in changes.nodes],
            "delete_session_ids": list(changes.delete_session_ids),
            "delete_branch_ids": list(changes.delete_branch_ids),
        }

    async def apply_changes(self, changes: ContextChanges) -> None:
        """追加一条原子增量记录，不读取或改写已有记录。"""
        self._write_json(self._record_path(changes.agent_name), self._serialize_changes(changes))

    async def load_checkpoint(
        self,
        agent_name: str,
        *,
        snapshot_payloads: SnapshotPayloads = "all",
    ) -> ContextCheckpoint | None:
        """按顺序重放 Agent 的 v3 变更记录。"""
        changes_dir = self._changes_dir(agent_name)
        if not changes_dir.exists():
            agent_dir = self._agent_dir(agent_name)
            if agent_dir.exists() and any(agent_dir.iterdir()):
                raise RuntimeError(
                    "Legacy context checkpoint detected; delete it and rebuild explicitly"
                )
            return None

        sessions: dict[str, Any] = {}
        branches: dict[str, Any] = {}
        nodes: dict[str, Any] = {}
        active_session_id: str | None = None
        records = sorted(path for path in changes_dir.iterdir() if not path.name.endswith(".tmp"))
        for path in records:
            raw = self._read_json(path)
            if not isinstance(raw, dict) or raw.get("schema_version") != 3:
                raise RuntimeError("Unsupported context checkpoint schema; rebuild explicitly")
            if raw.get("agent_name") != agent_name:
                raise RuntimeError("Context change record belongs to another agent")
            for branch_id in raw.get("delete_branch_ids", []):
                branches.pop(branch_id, None)
            for session_id in raw.get("delete_session_ids", []):
                sessions.pop(session_id, None)
                branches = {
                    key: branch
                    for key, branch in branches.items()
                    if branch.session_id != session_id
                }
                nodes = {key: node for key, node in nodes.items() if node.session_id != session_id}
            for item in raw.get("sessions", []):
                session = deserialize_action_session(item)
                sessions[session.session_id] = session
            for item in raw.get("branches", []):
                branch = deserialize_branch(item)
                branches[branch.branch_id] = branch
            for item in raw.get("nodes", []):
                node = deserialize_node(item)
                nodes.setdefault(node.id, node)
            if raw.get("active_session_id") is not None:
                active_session_id = raw["active_session_id"]

        if not records:
            return None
        if active_session_id is None:
            raise RuntimeError("Context checkpoint has no active session")
        checkpoint = ContextCheckpoint(
            agent_name=agent_name,
            active_session_id=active_session_id,
            sessions=tuple(sessions.values()),
            branches=tuple(branches.values()),
            nodes=tuple(nodes.values()),
        )
        return project_snapshot_payloads(checkpoint, snapshot_payloads)

    async def delete_checkpoint(self, agent_name: str) -> None:
        """删除指定 Agent 的全部 JSON 记录。"""
        agent_dir = self._agent_dir(agent_name)
        if agent_dir.exists():
            shutil.rmtree(agent_dir)

    async def list_agents(self) -> list[str]:
        """列出至少包含一条 v3 变更记录的 Agent。

        同目录下存在旧布局（nodes.json 等无 changes-v3 的 Agent 目录）
        时打点告警：这类数据对 v3 不可见但仍在磁盘上，调用方可据此
        提示显式重建，避免"数据还在却静默消失"。
        """
        if not self._run_dir.exists():
            return []
        agents: list[str] = []
        for directory in self._run_dir.iterdir():
            if not directory.is_dir():
                continue
            changes_dir = directory / "changes-v3"
            if changes_dir.is_dir() and any(changes_dir.iterdir()):
                agents.append(directory.name)
            elif any(directory.iterdir()):
                logger.warning(
                    "Agent '%s' has pre-v3 context data invisible to changes-v3; "
                    "delete it and rebuild explicitly",
                    directory.name,
                )
        return sorted(agents)
