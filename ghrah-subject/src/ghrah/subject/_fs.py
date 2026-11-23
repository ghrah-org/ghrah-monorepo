# SPDX-FileCopyrightText: 2026 chenxya <chenxya@ghrah.org>
#
# SPDX-License-Identifier: Apache-2.0

"""跨平台文件系统工具（Subject 内部共享）。

解决三个 POSIX 假设：
- 原子替换：写文件走 同目录 tmp + fsync + ``os.replace``。Windows 上目标被
  AV/索引器短暂占用时 ``os.replace`` 抛 ``PermissionError``，做小步重试。
- 可写性探针：``os.access(path, os.W_OK)`` 在 Windows 不查 ACL，语义失真；
  用真实创建临时文件后删除的探针替代。
"""

from __future__ import annotations

import os
import tempfile
import time
from pathlib import Path

__all__ = ["atomic_write_text", "is_writable"]

_REPLACE_RETRIES = 5
_REPLACE_RETRY_DELAY_S = 0.05


def atomic_write_text(path: str | Path, content: str) -> None:
    """原子写文本文件（UTF-8 + LF）：同目录 tmp + fsync + os.replace。

    崩溃时不会留下半写目标文件；Windows 上目标被短暂锁定时重试
    ``_REPLACE_RETRIES`` 次（间隔 ``_REPLACE_RETRY_DELAY_S`` 秒）。

    换行固定为 LF：文本产物要能跨平台逐字节复现（同一条内容在 Windows 与
    Linux 上写出的文件必须一致），否则能力包文本源、快照与夹具在 Windows 上
    会被 CRLF 污染。调用方显式给出的 ``\\r\\n`` 原样保留（``newline="\\n"``
    只关闭自动翻译）。

    Args:
        path: 目标文件路径（父目录不存在时自动创建）。
        content: 写入的文本内容。
    """
    target = Path(path)
    target.parent.mkdir(parents=True, exist_ok=True)
    fd, tmp_name = tempfile.mkstemp(
        dir=str(target.parent), prefix=f".{target.name}.", suffix=".tmp"
    )
    tmp_path = Path(tmp_name)
    try:
        with os.fdopen(fd, "w", encoding="utf-8", newline="\n") as f:
            f.write(content)
            f.flush()
            os.fsync(f.fileno())
        _replace_with_retry(tmp_path, target)
    except BaseException:
        try:
            tmp_path.unlink()
        except OSError:
            pass
        raise


def _replace_with_retry(src: Path, dst: Path) -> None:
    last_exc: PermissionError | None = None
    for _ in range(_REPLACE_RETRIES):
        try:
            os.replace(src, dst)
            return
        except PermissionError as exc:  # Windows: dst 被 AV/索引器占用
            last_exc = exc
            time.sleep(_REPLACE_RETRY_DELAY_S)
    assert last_exc is not None
    raise last_exc


def is_writable(dir_path: str | Path) -> bool:
    """目录是否真实可写（创建临时文件后删除的探针）。

    替代 ``os.access(path, os.W_OK)``：后者在 Windows 上不查 ACL，语义失真。
    """
    try:
        fd, probe = tempfile.mkstemp(dir=str(dir_path))
    except OSError:
        return False
    try:
        os.close(fd)
    except OSError:
        pass
    try:
        os.unlink(probe)
    except OSError:
        pass
    return True
