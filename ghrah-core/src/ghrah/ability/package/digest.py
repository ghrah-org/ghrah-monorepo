# SPDX-FileCopyrightText: 2026 chenxya <chenxya@ghrah.org>
#
# SPDX-License-Identifier: Apache-2.0
"""版本目录整树摘要：``sha256-tree-v1``。

``package_digest`` 是**运行版本身份**：同一个版本目录内容相同，摘要就相同；
改动任一普通文件的字节，摘要就变。算法只有一份实现，生成器、安装器、
``verify`` 与锁的一致性校验都从这里取，避免"三处各算一次、规则悄悄分叉"。

摘要输入是**逐文件清单**（相对路径 → 文件字节 ``sha256``），拼接顺序按
NFC 归一后的路径码点升序，与磁盘枚举顺序无关：

```text
package_digest = "sha256:" + sha256(concat(
    "file\\0" + path + "\\0" + files[path] + "\\n" for path in sorted(files)
))
```

其中 ``files[path]`` 自带 ``sha256:`` 前缀。路径一律 ``/`` 分隔、NFC 归一；
不记权限位、mtime 或 owner。文件集枚举复用
:func:`ghrah.ability.package.paths.ensure_portable_tree`：排除 ``.git/``、
缓存目录与已知垃圾文件，拒绝符号链接/junction 与非普通文件。
"""

from __future__ import annotations

import hashlib
import re
from collections.abc import Mapping
from pathlib import Path

from ghrah.ability.package.paths import ensure_portable_tree

__all__ = [
    "DIGEST_ALGORITHM",
    "SHA256_DIGEST_PATTERN",
    "collect_files",
    "file_digest",
    "tree_digest",
]

DIGEST_ALGORITHM: str = "sha256-tree-v1"
"""整树摘要算法标识（v1 冻结值，写进安装记录的 ``digest_algorithm``）。"""

SHA256_DIGEST_PATTERN: re.Pattern[str] = re.compile(r"^sha256:[0-9a-f]{64}$")
"""单个文件摘要的冻结形状：``sha256:<64 位小写十六进制>``。"""


def file_digest(path: str | Path) -> str:
    """单个文件的字节摘要。

    Args:
        path: 文件路径。

    Returns:
        ``sha256:<hex>``。

    Raises:
        OSError: 文件不可读。
    """
    return "sha256:" + hashlib.sha256(Path(path).read_bytes()).hexdigest()


def collect_files(root: str | Path) -> dict[str, str]:
    """枚举整树文件并计算逐文件摘要。

    Args:
        root: 版本目录（或源包根）。

    Returns:
        NFC 归一后的 ``/`` 相对路径 → ``sha256:<hex>``；键按码点升序。

    Raises:
        PathRuleError: 树包含符号链接/junction、非普通文件、不可移植路径，
            或文本源带 BOM / CRLF（由 :func:`ensure_portable_tree` 判定）。
    """
    return {relative: file_digest(full) for relative, full in ensure_portable_tree(Path(root))}


def tree_digest(files: Mapping[str, str]) -> str:
    """由逐文件清单复算整树摘要。

    Args:
        files: ``相对路径 → "sha256:<hex>"`` 映射；值须自带 ``sha256:`` 前缀
            （与 :func:`file_digest` / :func:`collect_files` 同口径）。

    Returns:
        ``sha256:<hex>`` 的整树摘要。入参映射的顺序不影响结果。
    """
    digest = hashlib.sha256()
    for path in sorted(files):
        digest.update(f"file\0{path}\0{files[path]}\n".encode())
    return "sha256:" + digest.hexdigest()
