# SPDX-FileCopyrightText: 2026 chenxya <chenxya@ghrah.org>
#
# SPDX-License-Identifier: Apache-2.0

"""Project 级附件库：内容寻址存储（sha256）+ sidecar 元数据。

存储布局（Project Root 内，独立于 Agent workspace）::

    <project_root>/attachments/<sha256>      原始字节
    <project_root>/attachments/<sha256>.json sidecar：mime_type/filename/size/created_at

设计要点：

- **内容寻址**：``attachment_id`` 就是内容的 sha256 hex（64 字符）。同内容
  重复上传幂等——返回同一个 id，且保留首次落盘时的 sidecar 元数据
  （filename/mime_type/created_at 以第一次为准）。
- **原子写**：先写同目录临时文件并 ``fsync``，再 ``os.replace`` 到最终名；
  失败时清理临时文件，绝不留下半截附件。
- **防穿越**：``attachment_id`` 只接受 64 位小写 hex；文件名由 id 派生，
  不拼接任何用户输入。
- **元数据只是 sidecar，不参与寻址**：sidecar 缺失/损坏时按字节文件兜底
  读出 size（mtime 作 created_at），不把附件判成不存在。
"""

from __future__ import annotations

import asyncio
import logging
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
)
from ghrah.subject.project.paths import ProjectPaths

__all__ = [
    "ATTACHMENT_ID_RE",
    "AttachmentRecord",
    "AttachmentStore",
    "is_valid_attachment_id",
    "project_attachment_store",
]

logger = logging.getLogger(__name__)

ATTACHMENT_ID_RE = CONTENT_ID_RE
"""附件 id 形状（= 内容 sha256 hex）；与产物 id 共用同一规则。"""

_SIDECAR_SUFFIX = ".json"
_FALLBACK_MIME_TYPE = "application/octet-stream"


def is_valid_attachment_id(value: object) -> bool:
    """``attachment_id`` 是否为合法 sha256 hex（同时用于防路径穿越）。"""
    return is_content_id(value)


def _now_iso() -> str:
    return datetime.now(tz=UTC).isoformat()


@dataclass(frozen=True)
class AttachmentRecord:
    """一个附件的稳定描述（id 即内容哈希）。"""

    attachment_id: str
    mime_type: str
    filename: str
    size: int
    created_at: str

    def to_wire(self) -> dict[str, Any]:
        """上传回执的 JSON 形状（``{attachment_id, size, mime_type, filename}``）。"""
        return {
            "attachment_id": self.attachment_id,
            "size": self.size,
            "mime_type": self.mime_type,
            "filename": self.filename,
        }


class AttachmentStore:
    """单 Project 的附件库。一个实例只绑定一个 attachments 目录。"""

    def __init__(self, directory: Path) -> None:
        self._directory = directory

    @classmethod
    def for_project(cls, project_root_locator: str) -> AttachmentStore:
        return cls(ProjectPaths.from_locator(project_root_locator).attachments_dir)

    @property
    def directory(self) -> Path:
        return self._directory

    def path_for(self, attachment_id: str) -> Path:
        """附件字节文件路径；调用方必须先校验 ``attachment_id`` 形状。"""
        if not is_valid_attachment_id(attachment_id):
            raise ValueError(f"invalid attachment_id: {attachment_id!r}")
        return self._directory / attachment_id

    def _sidecar_for(self, attachment_id: str) -> Path:
        return self._directory / f"{attachment_id}{_SIDECAR_SUFFIX}"

    async def save(
        self,
        data: bytes,
        *,
        mime_type: str,
        filename: str = "",
    ) -> AttachmentRecord:
        """写入（或复用）一份附件，返回其记录；同内容幂等。"""
        attachment_id = content_id_for(data)
        record = await asyncio.to_thread(
            self._save_sync,
            attachment_id,
            data,
            mime_type,
            filename,
        )
        return record

    def _save_sync(
        self,
        attachment_id: str,
        data: bytes,
        mime_type: str,
        filename: str,
    ) -> AttachmentRecord:
        self._directory.mkdir(parents=True, exist_ok=True)
        path = self.path_for(attachment_id)
        sidecar = self._sidecar_for(attachment_id)
        if not path.exists():
            self._atomic_write(path, data)
        if not sidecar.exists():
            record = AttachmentRecord(
                attachment_id=attachment_id,
                mime_type=mime_type,
                filename=filename,
                size=len(data),
                created_at=_now_iso(),
            )
            sidecar_payload = {
                **record.to_wire(),
                "created_at": record.created_at,
            }
            self._atomic_write(sidecar, dump_json_bytes(sidecar_payload))
        existing = self._read_sidecar(attachment_id)
        if existing is not None:
            return existing
        return AttachmentRecord(
            attachment_id=attachment_id,
            mime_type=mime_type,
            filename=filename,
            size=len(data),
            created_at=_now_iso(),
        )

    async def load(self, attachment_id: str) -> AttachmentRecord | None:
        """读取附件元数据；字节文件不存在时返回 ``None``。"""
        return await asyncio.to_thread(self._load_sync, attachment_id)

    def _load_sync(self, attachment_id: str) -> AttachmentRecord | None:
        path = self.path_for(attachment_id)
        try:
            stat = path.stat()
        except FileNotFoundError:
            return None
        record = self._read_sidecar(attachment_id)
        if record is not None:
            return record
        return AttachmentRecord(
            attachment_id=attachment_id,
            mime_type=_FALLBACK_MIME_TYPE,
            filename="",
            size=stat.st_size,
            created_at=datetime.fromtimestamp(stat.st_mtime, tz=UTC).isoformat(),
        )

    def _read_sidecar(self, attachment_id: str) -> AttachmentRecord | None:
        payload = read_json_dict(self._sidecar_for(attachment_id))
        if payload is None:
            return None
        return AttachmentRecord(
            attachment_id=attachment_id,
            mime_type=str(payload.get("mime_type") or _FALLBACK_MIME_TYPE),
            filename=str(payload.get("filename") or ""),
            size=int(payload.get("size") or 0),
            created_at=str(payload.get("created_at") or ""),
        )

    def _atomic_write(self, target: Path, payload: bytes) -> None:
        """同目录临时文件 → fsync → os.replace，避免半截文件。"""
        atomic_write_bytes(target, payload)


def project_attachment_store(project_root_locator: str) -> AttachmentStore:
    """由 Project Root locator 构造附件库（每个请求按项目根惰性解析）。"""
    return AttachmentStore.for_project(project_root_locator)
