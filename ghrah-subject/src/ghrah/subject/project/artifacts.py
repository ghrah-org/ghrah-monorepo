# SPDX-FileCopyrightText: 2026 chenxya <chenxya@ghrah.org>
#
# SPDX-License-Identifier: Apache-2.0

"""Project 级产物库：内容寻址的只读发布副本（sha256）+ sidecar 元数据。

存储布局（Project Root 内，独立于 Agent workspace）::

    <project_root>/artifacts/<artifact_id>      原始字节（artifact_id = sha256 hex）
    <project_root>/artifacts/<artifact_id>.json sidecar：
        filename/mime_type/size/title/published_at/source

权威边界：工作区文件仍归 Agent 工作区所有；``artifacts/`` 只是**发布副本**，
不可变、按内容去重、不随工作区后续变化而变。删除/GC 不在本切片。

设计要点与附件库一致（共用 :mod:`ghrah.subject.project.content_store`）：

- **内容寻址**：同一份字节重复发布返回同一个 id，保留首次 sidecar
  （filename/title/published_at/source 以第一次发布为准）。
- **原子写**：临时文件 + ``fsync`` + ``os.replace``，失败清理。
- **防穿越**：``artifact_id`` 只接受 sha256 hex；文件名由 id 派生，不拼接
  用户输入。
- ``source = {workspace_id, path, agent_id?}``：``path`` 是发布时的
  工作区相对路径（POSIX 形态），只作溯源信息，不参与寻址。
"""

from __future__ import annotations

import asyncio
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from ghrah.subject.project.content_store import (
    CONTENT_ID_RE,
    atomic_write_bytes,
    content_id_for,
    dump_json_bytes,
    is_content_id,
    read_json_dict,
    sidecar_suffix,
)
from ghrah.subject.project.paths import ProjectPaths

__all__ = [
    "ARTIFACT_ID_RE",
    "MAX_ARTIFACTS_LISTED",
    "ArtifactRecord",
    "ArtifactStore",
    "is_valid_artifact_id",
    "project_artifact_store",
]

ARTIFACT_ID_RE = CONTENT_ID_RE
"""产物 id 形状（= 内容 sha256 hex）；与附件 id 共用同一规则。"""

MAX_ARTIFACTS_LISTED = 200
"""列表返回上限（按 published_at 倒序截断）。"""

_FALLBACK_MIME_TYPE = "application/octet-stream"
_SIDECAR_SUFFIX = sidecar_suffix()


def is_valid_artifact_id(value: object) -> bool:
    """``artifact_id`` 是否为合法 sha256 hex（同时用于防路径穿越）。"""
    return is_content_id(value)


def _now_iso() -> str:
    return datetime.now(tz=UTC).isoformat()


@dataclass(frozen=True)
class ArtifactRecord:
    """一个已发布产物的稳定描述（id 即内容哈希）。"""

    artifact_id: str
    filename: str
    mime_type: str
    size: int
    title: str
    published_at: str
    source: dict[str, Any]

    def to_wire(self) -> dict[str, Any]:
        """产物 JSON 形状（列表条目与发布回执共用）。"""
        return {
            "artifact_id": self.artifact_id,
            "filename": self.filename,
            "mime_type": self.mime_type,
            "size": self.size,
            "title": self.title,
            "published_at": self.published_at,
            "source": dict(self.source),
        }


class ArtifactStore:
    """单 Project 的产物库。一个实例只绑定一个 artifacts 目录。"""

    def __init__(self, directory: Path) -> None:
        self._directory = directory

    @classmethod
    def for_project(cls, project_root_locator: str) -> ArtifactStore:
        return cls(ProjectPaths.from_locator(project_root_locator).artifacts_dir)

    @property
    def directory(self) -> Path:
        return self._directory

    def path_for(self, artifact_id: str) -> Path:
        """产物字节文件路径；调用方必须先校验 ``artifact_id`` 形状。"""
        if not is_valid_artifact_id(artifact_id):
            raise ValueError(f"invalid artifact_id: {artifact_id!r}")
        return self._directory / artifact_id

    def _sidecar_for(self, artifact_id: str) -> Path:
        return self._directory / f"{artifact_id}{_SIDECAR_SUFFIX}"

    async def publish(
        self,
        data: bytes,
        *,
        filename: str,
        mime_type: str,
        title: str = "",
        source: dict[str, Any] | None = None,
    ) -> ArtifactRecord:
        """发布（或复用）一份产物，返回其记录；同内容幂等。"""
        artifact_id = content_id_for(data)
        return await asyncio.to_thread(
            self._publish_sync,
            artifact_id,
            data,
            filename,
            mime_type,
            title,
            dict(source or {}),
        )

    def _publish_sync(
        self,
        artifact_id: str,
        data: bytes,
        filename: str,
        mime_type: str,
        title: str,
        source: dict[str, Any],
    ) -> ArtifactRecord:
        self._directory.mkdir(parents=True, exist_ok=True)
        path = self.path_for(artifact_id)
        sidecar = self._sidecar_for(artifact_id)
        if not path.exists():
            self._atomic_write(path, data)
        if not sidecar.exists():
            record = ArtifactRecord(
                artifact_id=artifact_id,
                filename=filename,
                mime_type=mime_type,
                size=len(data),
                title=title,
                published_at=_now_iso(),
                source=source,
            )
            self._atomic_write(
                sidecar,
                dump_json_bytes(
                    {
                        "filename": record.filename,
                        "mime_type": record.mime_type,
                        "size": record.size,
                        "title": record.title,
                        "published_at": record.published_at,
                        "source": record.source,
                    }
                ),
            )
        existing = self._read_sidecar(artifact_id)
        if existing is not None:
            return existing
        return ArtifactRecord(
            artifact_id=artifact_id,
            filename=filename,
            mime_type=mime_type,
            size=len(data),
            title=title,
            published_at=_now_iso(),
            source=source,
        )

    async def load(self, artifact_id: str) -> ArtifactRecord | None:
        """读取产物元数据；字节文件不存在时返回 ``None``。"""
        return await asyncio.to_thread(self._load_sync, artifact_id)

    async def list(self, limit: int = MAX_ARTIFACTS_LISTED) -> list[ArtifactRecord]:
        """按 ``published_at`` 倒序列出产物（上限 ``limit``，至多 200）。"""
        return await asyncio.to_thread(self._list_sync, limit)

    def _list_sync(self, limit: int) -> list[ArtifactRecord]:
        try:
            entries = list(self._directory.iterdir())
        except FileNotFoundError:
            return []
        records: list[ArtifactRecord] = []
        for entry in entries:
            name = entry.name
            if name.startswith(".") or name.endswith(_SIDECAR_SUFFIX):
                continue
            if not is_valid_artifact_id(name):
                continue
            record = self._load_sync(name)
            if record is not None:
                records.append(record)
        records.sort(key=lambda item: (item.published_at, item.filename), reverse=True)
        return records[: max(1, min(limit, MAX_ARTIFACTS_LISTED))]

    def _load_sync(self, artifact_id: str) -> ArtifactRecord | None:
        path = self.path_for(artifact_id)
        try:
            stat = path.stat()
        except FileNotFoundError:
            return None
        record = self._read_sidecar(artifact_id)
        if record is not None:
            return record
        return ArtifactRecord(
            artifact_id=artifact_id,
            filename="",
            mime_type=_FALLBACK_MIME_TYPE,
            size=stat.st_size,
            title="",
            published_at=datetime.fromtimestamp(stat.st_mtime, tz=UTC).isoformat(),
            source={},
        )

    def _read_sidecar(self, artifact_id: str) -> ArtifactRecord | None:
        payload = read_json_dict(self._sidecar_for(artifact_id))
        if payload is None:
            return None
        source = payload.get("source")
        return ArtifactRecord(
            artifact_id=artifact_id,
            filename=str(payload.get("filename") or ""),
            mime_type=str(payload.get("mime_type") or _FALLBACK_MIME_TYPE),
            size=int(payload.get("size") or 0),
            title=str(payload.get("title") or ""),
            published_at=str(payload.get("published_at") or ""),
            source=dict(source) if isinstance(source, dict) else {},
        )

    def _atomic_write(self, target: Path, payload: bytes) -> None:
        """同目录临时文件 → fsync → os.replace，避免半截文件。"""
        atomic_write_bytes(target, payload)


def project_artifact_store(project_root_locator: str) -> ArtifactStore:
    """由 Project Root locator 构造产物库（每个请求按项目根惰性解析）。"""
    return ArtifactStore.for_project(project_root_locator)
