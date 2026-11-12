# SPDX-FileCopyrightText: 2026 chenxya <chenxya@ghrah.org>
#
# SPDX-License-Identifier: Apache-2.0

"""内容寻址存储的共用小工具（附件库与产物库共享）。

两个库的语义一致：``<id>`` 是内容 sha256 hex，``<id>.json`` 是 sidecar
元数据；同内容幂等、首次元数据为准、原子写。差异只在 sidecar 字段，
因此把「id 校验 / 原子写 / sidecar 读写」抽到这里，避免两份拷贝漂移。
"""

from __future__ import annotations

import hashlib
import json
import os
import re
import uuid
from pathlib import Path
from typing import Any

__all__ = [
    "CONTENT_ID_RE",
    "atomic_write_bytes",
    "content_id_for",
    "dump_json_bytes",
    "is_content_id",
    "read_json_dict",
]

CONTENT_ID_RE = re.compile(r"^[0-9a-f]{64}$")

_SIDECAR_SUFFIX = ".json"


def is_content_id(value: object) -> bool:
    """是否为合法内容 id（sha256 hex）；同时用于防路径穿越。"""
    return isinstance(value, str) and CONTENT_ID_RE.fullmatch(value) is not None


def content_id_for(payload: bytes) -> str:
    """内容寻址 id：原始字节的 sha256 hex。"""
    return hashlib.sha256(payload).hexdigest()


def sidecar_suffix() -> str:
    return _SIDECAR_SUFFIX


def atomic_write_bytes(target: Path, payload: bytes) -> None:
    """同目录临时文件 → fsync → os.replace，失败清理，绝不留下半截文件。"""
    tmp = target.with_name(f".{target.name}.tmp-{uuid.uuid4().hex}")
    try:
        with tmp.open("wb") as handle:
            handle.write(payload)
            handle.flush()
            os.fsync(handle.fileno())
        os.replace(tmp, target)
    except BaseException:
        try:
            tmp.unlink(missing_ok=True)
        except OSError:
            pass
        raise


def dump_json_bytes(payload: dict[str, Any]) -> bytes:
    """sidecar 序列化：UTF-8、键有序（便于 diff/排障）、行尾换行。"""
    return (json.dumps(payload, ensure_ascii=False, sort_keys=True) + "\n").encode("utf-8")


def read_json_dict(path: Path) -> dict[str, Any] | None:
    """读 JSON sidecar；缺失/损坏/非对象一律返回 ``None``（调用方兜底）。"""
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except OSError, ValueError:
        return None
    return payload if isinstance(payload, dict) else None
