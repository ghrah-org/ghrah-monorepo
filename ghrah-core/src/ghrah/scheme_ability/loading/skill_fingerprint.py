# SPDX-FileCopyrightText: 2026 chenxya <chenxya@ghrah.org>
#
# SPDX-License-Identifier: Apache-2.0

"""能力包整树指纹：manifest 观测、两枚投影摘要、入口哨兵与差异比对。

一次观测产出一份 **manifest**（包结构 + 逐文件摘要 + mtime），它是唯一权威；
所有摘要都是 manifest 的**派生投影**，因此永远可以从 manifest 复算：

- ``content_digest``（``sha256-tree-v1``）：**内容投影**摘要——逐文件行去掉 mtime
  列。确定性：同一份内容在任何机器、任何时刻重装都得到同一个值，用来回答
  "这是不是同一份内容快照"；
- ``snapshot_digest``（``sha256-snapshot-v1``）：**快照投影**摘要——保留 mtime 列。
  本机状态：跨机器不可比（mtime 由落地方式决定），用来回答"这份安装有没有被动过"；
- 入口哨兵（``sha256-entry-v1``）：只覆盖**定义面 + 正文**两份文件的派生摘要，
  供发现面廉价查询——它同样可从 manifest 复算，不额外登记。

**manifest 文本格式**（UTF-8，LF 分隔，按 relpath 的 UTF-8 字节序排序）::

    ghrah-skill-tree-v1                     ← 魔数：域分隔 + 输入格式版本
    skill<TAB><full_name>
    d<TAB><relpath>                         ← 目录（含空目录）
    f<TAB><relpath><TAB><size><TAB><sha256><TAB><mtime_ns>
    l<TAB><relpath><TAB><target>            ← 符号链接：记目标，不跟随

路径一律为相对包根目录的 **POSIX** 形式并做 **NFC 归一化**（macOS 存 NFD、
Linux 存 NFC，不归一化会让同一份包跨平台得到不同指纹）；含 TAB/换行的路径
无法无歧义编码，出现即降级（不产出摘要），不静默丢弃。

**排除清单是显式的**（:data:`EXCLUDED_DIR_NAMES` / :data:`EXCLUDED_FILE_NAMES` /
:data:`EXCLUDED_FILE_SUFFIXES`）：``ability.lock.yaml``（自我指涉——它记录指纹，
若被算进指纹则写完即失效）与 ``__pycache__`` 一类缓存产物（否则运行一次 Python
就改指纹，造成永久假漂移）必须排除，其余一律收集。
"""

from __future__ import annotations

import hashlib
import os
import unicodedata
from collections.abc import Mapping
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from ghrah.scheme_ability.loading.ability_schema import (
    DEFINITION_FILE_NAME,
    LOCK_FILE_NAME,
    SKILL_BODY_DIR,
    SKILL_FILE_NAME,
)
from ghrah.scheme_ability.loading.skill_errors import SkillLockError, SkillParseError

__all__ = [
    "CONTENT_ALGORITHM",
    "DEFAULT_MAX_ENTRIES",
    "DEFAULT_MAX_TOTAL_BYTES",
    "ENTRY_ALGORITHM",
    "ENTRY_BODY_RELPATH",
    "EXCLUDED_DIR_NAMES",
    "EXCLUDED_FILE_NAMES",
    "EXCLUDED_FILE_SUFFIXES",
    "FILE_DIGEST_PREFIX",
    "MANIFEST_MAGIC",
    "SNAPSHOT_ALGORITHM",
    "BundleEntry",
    "BundleManifest",
    "ManifestChange",
    "ManifestDegrade",
    "build_manifest",
    "diff_manifests",
    "entry_digest",
    "entry_relpaths",
    "fingerprint_entry",
    "parse_degrade",
    "parse_manifest",
]

MANIFEST_MAGIC: str = "ghrah-skill-tree-v1"
"""manifest 首行魔数：域分隔 + 输入格式版本（改格式时递增，避免误判成内容漂移）。"""

ENTRY_MAGIC: str = "ghrah-skill-entry-v1"
"""入口哨兵的域分隔串（与整树摘要不共用输入空间）。"""

CONTENT_ALGORITHM: str = "sha256-tree-v1"
SNAPSHOT_ALGORITHM: str = "sha256-snapshot-v1"
ENTRY_ALGORITHM: str = "sha256-entry-v1"

FILE_DIGEST_PREFIX: str = "sha256:"
"""逐文件摘要前缀（与 ``ghrah.scheme_ability.loading.skill_lock`` 早期版本的值格式一致）。"""

EXCLUDED_DIR_NAMES: frozenset[str] = frozenset(
    {".git", "node_modules", "__pycache__", ".venv", "dist"}
)
"""整棵剪枝的目录名（与 ``schema.ability.fs.operations.search_files`` 的跳读口径一致）。"""

EXCLUDED_FILE_NAMES: frozenset[str] = frozenset(
    {
        LOCK_FILE_NAME,
        ".DS_Store",
        "Thumbs.db",
    }
)
"""排除的文件名：``ability.lock.yaml`` 是自我指涉，其余是 OS 噪声。"""

EXCLUDED_FILE_SUFFIXES: tuple[str, ...] = (".pyc", ".pyo", ".swp", ".swo", "~")
"""排除的文件后缀：字节码缓存与编辑器残留（``*~`` 是备份文件）。"""

DEFAULT_MAX_ENTRIES: int = 1024
"""manifest 条目数上限；超过即降级（不产出摘要，绝不产出"部分树摘要"冒充全树）。"""

DEFAULT_MAX_TOTAL_BYTES: int = 64 * 1024 * 1024
"""纳入观测的文件总字节上限；超过即降级。"""

_CHUNK_SIZE: int = 1024 * 1024

DEGRADE_ENTRY_LIMIT: str = "entry_limit"
DEGRADE_BYTE_LIMIT: str = "byte_limit"
DEGRADE_UNREADABLE: str = "unreadable"
DEGRADE_UNSAFE_PATH: str = "unsafe_path"

_UNSAFE_PATH_CHARS: tuple[str, ...] = ("\t", "\n", "\r")

ENTRY_BODY_RELPATH: str = f"{SKILL_BODY_DIR}/{SKILL_FILE_NAME}"
"""新格式正文相对路径：``skills/SKILL.md``。"""


@dataclass(frozen=True)
class ManifestDegrade:
    """降级原因：观测不完整时**不产出任何摘要**，只如实记账。"""

    reason: str
    """``entry_limit`` / ``byte_limit`` / ``unreadable`` / ``unsafe_path``。"""

    entries: int
    """已观测到的条目数（因提前中止，不一定是全树的真实条目数）。"""

    total_bytes: int
    """已观测到的文件总字节数。"""

    limit: int | None = None
    """触发的上限（``entry_limit`` / ``byte_limit`` 时有值）。"""

    detail: str = ""
    """人类可读的补充说明（如不可读的路径与原因）。"""

    def to_mapping(self) -> dict[str, Any]:
        """转成落盘用的映射（省略空值，保持 lock 精简）。"""
        data: dict[str, Any] = {
            "reason": self.reason,
            "entries": self.entries,
            "total_bytes": self.total_bytes,
        }
        if self.limit is not None:
            data["limit"] = self.limit
        if self.detail:
            data["detail"] = self.detail
        return data


@dataclass(frozen=True)
class BundleEntry:
    """manifest 里的一条观测（目录 / 文件 / 符号链接）。"""

    kind: str
    """``d``（目录）/ ``f``（文件）/ ``l``（符号链接）。"""

    relpath: str
    """相对包根目录的 POSIX 路径（NFC 归一化）。"""

    size: int | None = None
    """文件字节数（``kind == "f"``）。"""

    digest: str | None = None
    """逐文件内容摘要 ``sha256:<hex>``（``kind == "f"``）。"""

    mtime_ns: int | None = None
    """文件修改时间，整数纳秒（``kind == "f"``）。

    用整数纳秒而非 float 秒或格式化日期：float 序列化有精度/表示风险，
    格式化日期会引入时区与本地化差异。
    """

    target: str | None = None
    """符号链接目标（``kind == "l"``）；只记目标，不跟随（避免读到包外内容或成环）。"""

    def content_line(self) -> str:
        """内容投影的条目行（文件行不含 mtime 列）。"""
        if self.kind == "f":
            return f"f\t{self.relpath}\t{self.size}\t{self.digest}"
        if self.kind == "d":
            return f"d\t{self.relpath}"
        return f"l\t{self.relpath}\t{self.target}"

    def snapshot_line(self) -> str:
        """快照投影的条目行（文件行含 mtime 列）。"""
        if self.kind == "f":
            return f"f\t{self.relpath}\t{self.size}\t{self.digest}\t{self.mtime_ns}"
        return self.content_line()


@dataclass(frozen=True)
class ManifestChange:
    """记录值与当前值之间的一处差异。"""

    relpath: str
    kind: str
    """``added`` / ``removed`` / ``kind`` / ``content`` / ``mtime`` / ``link``。"""


@dataclass(frozen=True)
class BundleManifest:
    """一次包观测的结果：条目清单 + 降级状态 + 观测期问题。

    ``entries`` 是权威观测；:attr:`content_digest` / :attr:`snapshot_digest` /
    :func:`entry_digest` 都是它的派生投影。降级（``degraded is not None``）时
    ``entries`` 为空且不产出任何摘要。
    """

    full_name: str
    entries: tuple[BundleEntry, ...] = ()
    degraded: ManifestDegrade | None = None
    issues: tuple[str, ...] = ()

    @property
    def complete(self) -> bool:
        """观测是否完整（完整才产出摘要）。"""
        return self.degraded is None

    @property
    def entry_count(self) -> int:
        """条目数（含目录与符号链接）。"""
        return len(self.entries)

    @property
    def files(self) -> tuple[BundleEntry, ...]:
        """文件条目。"""
        return tuple(entry for entry in self.entries if entry.kind == "f")

    @property
    def total_bytes(self) -> int:
        """纳入观测的文件总字节数。"""
        return sum(entry.size or 0 for entry in self.files)

    @property
    def max_mtime_ns(self) -> int | None:
        """整包最新修改时间（逐文件 mtime 的最大值，派生值，不参与摘要）。"""
        values = [entry.mtime_ns for entry in self.files if entry.mtime_ns is not None]
        return max(values) if values else None

    @property
    def text(self) -> str | None:
        """完整观测文本（**快照投影**，含 mtime 列）；降级时为 None。

        这是落盘进 lock 的权威记录。
        """
        if self.degraded is not None:
            return None
        lines = [MANIFEST_MAGIC, f"skill\t{self.full_name}"]
        lines.extend(entry.snapshot_line() for entry in self.entries)
        return "\n".join(lines) + "\n"

    @property
    def content_text(self) -> str | None:
        """内容投影文本（文件行去掉 mtime 列）；降级时为 None。"""
        if self.degraded is not None:
            return None
        lines = [MANIFEST_MAGIC, f"skill\t{self.full_name}"]
        lines.extend(entry.content_line() for entry in self.entries)
        return "\n".join(lines) + "\n"

    @property
    def content_digest(self) -> str | None:
        """内容摘要：结构 + 内容，不含时间——跨机器 / 跨重装可复现。"""
        text = self.content_text
        return None if text is None else _digest_text(text, CONTENT_ALGORITHM)

    @property
    def snapshot_digest(self) -> str | None:
        """快照摘要：结构 + 内容 + mtime——本机状态，跨机器不可比。"""
        text = self.text
        return None if text is None else _digest_text(text, SNAPSHOT_ALGORITHM)

    def find(self, relpath: str) -> BundleEntry | None:
        """按相对路径精确查条目（NFC 归一化后比较）。"""
        target = _normalize(relpath)
        for entry in self.entries:
            if entry.relpath == target:
                return entry
        return None


def _normalize(value: str) -> str:
    """路径 NFC 归一化（跨平台文件名归一化差异的唯一收敛点）。"""
    return unicodedata.normalize("NFC", value)


def _digest_text(text: str, algorithm: str) -> str:
    """对投影文本做一次摘要，带算法前缀。"""
    return f"{algorithm}:{hashlib.sha256(text.encode('utf-8')).hexdigest()}"


def _relpath(path: Path, root: Path) -> str:
    """相对包根的 POSIX 路径（NFC 归一化）。"""
    return _normalize(path.relative_to(root).as_posix())


def _is_link_like(path: Path) -> bool:
    """是否为"链接类"条目（POSIX 符号链接，或 Windows 目录联接 / 符号链接）。

    不能只看 :meth:`pathlib.Path.is_symlink`：Windows 上它对**目录联接**
    （junction）返回 False，``os.walk`` 于是会深入联接内部——把包外内容算进指纹，
    指向祖先时还会成环。所以按"重解析点"这一更宽的判据识别
    （``st_reparse_tag`` 为 0 或不存在即普通条目）。
    """
    if path.is_symlink():
        return True
    try:
        return getattr(path.lstat(), "st_reparse_tag", 0) != 0
    except OSError:
        return False


def _file_digest(path: Path) -> tuple[str, int, int]:
    """流式算单文件摘要，返回 ``(digest, size, mtime_ns)``。

    分块读取而非整读内存：bundle 里可能有较大的 asset。
    """
    hasher = hashlib.sha256()
    with path.open("rb") as handle:
        while chunk := handle.read(_CHUNK_SIZE):
            hasher.update(chunk)
    stat = path.stat()
    return f"{FILE_DIGEST_PREFIX}{hasher.hexdigest()}", stat.st_size, stat.st_mtime_ns


def _read_link(path: Path, issues: list[str]) -> str | None:
    """读符号链接目标（NFC 归一化）；失败记 issue 并跳过。"""
    try:
        return _normalize(os.readlink(path))
    except OSError as exc:
        issues.append(f"{path.name}: 读取符号链接失败: {exc}")
        return None


def entry_relpaths(
    *, definition_present: bool, body_relpath: str = ENTRY_BODY_RELPATH
) -> tuple[str, ...]:
    """入口哨兵覆盖的条目路径（固定顺序：定义面在前，正文在后）。

    新格式 = ``ability.yaml`` + 定义面声明的正文路径（默认 ``skills/SKILL.md``）；
    旧格式只有包根 ``SKILL.md``。

    Args:
        definition_present: 容器根有没有 ``ability.yaml``。
        body_relpath: 定义面 ``skills.<key>.body.path`` 的读数（``/`` 分隔）。
            调用方从声明里取——这里**不拼路径**，否则声明与哨兵会各说一套。

    Returns:
        相对容器根的条目路径元组，顺序固定（定义面在前）。
    """
    if definition_present:
        return (DEFINITION_FILE_NAME, body_relpath)
    return (SKILL_FILE_NAME,)


def _digest_entry(parts: list[str]) -> str:
    """入口哨兵：对入口条目的逐文件摘要按固定顺序再哈希。"""
    body = "\n".join([ENTRY_MAGIC, *parts]) + "\n"
    return f"{ENTRY_ALGORITHM}:{hashlib.sha256(body.encode('utf-8')).hexdigest()}"


def entry_digest(manifest: BundleManifest, *, body_relpath: str = ENTRY_BODY_RELPATH) -> str | None:
    """从 manifest 复算入口哨兵；入口文件缺失或观测降级时为 None。

    Args:
        manifest: 容器整树的观测结果。
        body_relpath: 定义面声明的正文路径（``/`` 分隔）；默认取本版约定。

    Returns:
        入口哨兵字符串；任一入口条目缺失、非普通文件或摘要是降级值时返回 None。
    """
    if manifest.degraded is not None:
        return None
    paths = entry_relpaths(
        definition_present=manifest.find(DEFINITION_FILE_NAME) is not None,
        body_relpath=body_relpath,
    )
    parts: list[str] = []
    for relpath in paths:
        entry = manifest.find(relpath)
        if entry is None or entry.kind != "f" or entry.digest is None:
            return None
        parts.append(f"f\t{entry.relpath}\t{entry.digest}")
    return _digest_entry(parts)


def fingerprint_entry(skill_dir: Path, *, body_relpath: str = ENTRY_BODY_RELPATH) -> str:
    """入口哨兵（廉价路径）：只读定义面 + 正文两份文件，不建整树 manifest。

    返回值与 :func:`entry_digest` 对同一目录的整树观测结果**必然相同**
    （两者共用 :func:`_digest_entry` 的输入口径），这是发现面与整树裁决
    不产生第二套口径的前提。

    Args:
        skill_dir: 容器根目录。
        body_relpath: 定义面声明的正文路径（``/`` 分隔）。调用方从声明里取，
            使哨兵覆盖的文件与读取正文用的是同一个文件。

    Returns:
        入口哨兵字符串。

    Raises:
        SkillParseError: 入口文件（定义面或正文）不存在。
    """
    definition = skill_dir / DEFINITION_FILE_NAME
    if definition.is_file():
        paths = (definition, skill_dir.joinpath(*body_relpath.split("/")))
    else:
        paths = (skill_dir / SKILL_FILE_NAME,)

    parts: list[str] = []
    for path in paths:
        if not path.is_file():
            raise SkillParseError(f"入口文件不存在: {path}")
        digest, _size, _mtime = _file_digest(path)
        parts.append(f"f\t{_relpath(path, skill_dir)}\t{digest}")
    return _digest_entry(parts)


def build_manifest(
    skill_dir: Path,
    *,
    full_name: str,
    max_entries: int = DEFAULT_MAX_ENTRIES,
    max_total_bytes: int = DEFAULT_MAX_TOTAL_BYTES,
) -> BundleManifest:
    """观测整个能力包目录，产出 manifest。

    两阶段：先走目录树收集结构（只 stat，不读内容），命中上限即提前中止；未降级
    才进入第二阶段流式算逐文件摘要。这样"大包"在付出读盘成本前就被拦下。

    Args:
        skill_dir: 能力包根目录。
        full_name: 身份全名（写进 manifest 的 ``skill`` 行）。
        max_entries: 条目数上限。
        max_total_bytes: 文件总字节上限。

    Returns:
        :class:`BundleManifest`；未降级时 :attr:`BundleManifest.text` 与两枚摘要可用。
    """
    observed: list[tuple[str, str, Path | None, int | None, int | None, str | None]] = []
    issues: list[str] = []
    total_bytes = 0
    degrade: ManifestDegrade | None = None

    for dirpath, dirnames, filenames in os.walk(skill_dir, topdown=True, followlinks=False):
        current = Path(dirpath)

        kept: list[str] = []
        for name in sorted(dirnames):
            child = current / name
            if name in EXCLUDED_DIR_NAMES:
                continue
            if _is_link_like(child):
                target = _read_link(child, issues)
                if target is not None:
                    observed.append(("l", _relpath(child, skill_dir), None, None, None, target))
                continue
            kept.append(name)
            observed.append(("d", _relpath(child, skill_dir), None, None, None, None))
        dirnames[:] = kept

        for name in sorted(filenames):
            child = current / name
            if name in EXCLUDED_FILE_NAMES or name.endswith(EXCLUDED_FILE_SUFFIXES):
                continue
            if _is_link_like(child):
                target = _read_link(child, issues)
                if target is not None:
                    observed.append(("l", _relpath(child, skill_dir), None, None, None, target))
                continue
            try:
                stat = child.stat()
            except OSError as exc:
                issues.append(f"{_relpath(child, skill_dir)}: stat 失败: {exc}")
                continue
            observed.append(
                ("f", _relpath(child, skill_dir), child, stat.st_size, stat.st_mtime_ns, None)
            )
            total_bytes += stat.st_size

        if len(observed) > max_entries:
            degrade = ManifestDegrade(
                reason=DEGRADE_ENTRY_LIMIT,
                entries=len(observed),
                total_bytes=total_bytes,
                limit=max_entries,
                detail=f"条目数超过上限 {max_entries}，未产出整树摘要",
            )
            break
        if total_bytes > max_total_bytes:
            degrade = ManifestDegrade(
                reason=DEGRADE_BYTE_LIMIT,
                entries=len(observed),
                total_bytes=total_bytes,
                limit=max_total_bytes,
                detail=f"文件总字节超过上限 {max_total_bytes}，未产出整树摘要",
            )
            break

    if degrade is None:
        unsafe = [
            item[1] for item in observed if any(char in item[1] for char in _UNSAFE_PATH_CHARS)
        ]
        if unsafe:
            degrade = ManifestDegrade(
                reason=DEGRADE_UNSAFE_PATH,
                entries=len(observed),
                total_bytes=total_bytes,
                detail=f"路径含 TAB/换行，无法无歧义编码: {unsafe[0]!r}",
            )

    entries: list[BundleEntry] = []
    if degrade is None:
        observed.sort(key=lambda item: item[1].encode("utf-8"))
        for kind, relpath, path, size, mtime_ns, target in observed:
            if kind != "f":
                entries.append(BundleEntry(kind=kind, relpath=relpath, target=target))
                continue
            assert path is not None
            try:
                digest, after_size, after_mtime = _file_digest(path)
            except OSError as exc:
                entries = []
                degrade = ManifestDegrade(
                    reason=DEGRADE_UNREADABLE,
                    entries=len(observed),
                    total_bytes=total_bytes,
                    detail=f"{relpath}: 内容读取失败: {exc}",
                )
                break
            if after_size != size or after_mtime != mtime_ns:
                issues.append(f"{relpath}: 观测期间被修改（以读取后的 size/mtime 记入）")
                size, mtime_ns = after_size, after_mtime
            entries.append(
                BundleEntry(kind="f", relpath=relpath, size=size, digest=digest, mtime_ns=mtime_ns)
            )

    return BundleManifest(
        full_name=full_name,
        entries=tuple(entries),
        degraded=degrade,
        issues=tuple(issues),
    )


def parse_manifest(text: str) -> tuple[str, tuple[BundleEntry, ...]]:
    """解析 manifest 文本，返回 ``(full_name, entries)``。

    Raises:
        SkillLockError: 魔数不匹配、缺少 skill 行、或条目行格式非法。
    """
    lines = text.split("\n")
    if lines and lines[-1] == "":
        lines.pop()
    if not lines or lines[0] != MANIFEST_MAGIC:
        raise SkillLockError(
            f"manifest 首行不是魔数 {MANIFEST_MAGIC!r}（无法确认格式版本，拒绝猜测）"
        )
    if len(lines) < 2 or not lines[1].startswith("skill\t"):
        raise SkillLockError("manifest 缺少 skill 行")

    full_name = lines[1].split("\t", 1)[1]
    return full_name, tuple(_parse_entry_line(line) for line in lines[2:])


def _parse_entry_line(line: str) -> BundleEntry:
    """解析一条条目行。"""
    parts = line.split("\t")
    kind = parts[0] if parts else ""
    if kind == "d" and len(parts) == 2:
        return BundleEntry(kind="d", relpath=parts[1])
    if kind == "l" and len(parts) == 3:
        return BundleEntry(kind="l", relpath=parts[1], target=parts[2])
    if kind == "f" and len(parts) == 5:
        try:
            size = int(parts[2])
            mtime_ns = int(parts[4])
        except ValueError as exc:
            raise SkillLockError(f"manifest 条目行的数值非法: {line!r}") from exc
        return BundleEntry(
            kind="f", relpath=parts[1], size=size, digest=parts[3], mtime_ns=mtime_ns
        )
    raise SkillLockError(f"manifest 条目行格式非法: {line!r}")


def parse_degrade(raw: Any) -> ManifestDegrade | None:
    """解析 lock 里的降级映射。

    Raises:
        SkillLockError: 映射结构或字段类型非法。
    """
    if raw is None:
        return None
    if not isinstance(raw, Mapping):
        raise SkillLockError("lock 的 manifest.degraded 不是映射")
    reason = raw.get("reason")
    entries = raw.get("entries")
    total_bytes = raw.get("total_bytes")
    limit = raw.get("limit")
    detail = raw.get("detail", "")
    if not isinstance(reason, str) or not reason:
        raise SkillLockError("lock 的 manifest.degraded 缺少 reason")
    if not isinstance(entries, int) or not isinstance(total_bytes, int):
        raise SkillLockError("lock 的 manifest.degraded 的 entries/total_bytes 不是整数")
    if limit is not None and not isinstance(limit, int):
        raise SkillLockError("lock 的 manifest.degraded 的 limit 不是整数")
    if not isinstance(detail, str):
        raise SkillLockError("lock 的 manifest.degraded 的 detail 不是字符串")
    return ManifestDegrade(
        reason=reason, entries=entries, total_bytes=total_bytes, limit=limit, detail=detail
    )


def diff_manifests(recorded: BundleManifest, current: BundleManifest) -> tuple[ManifestChange, ...]:
    """比对两份 manifest，返回按路径排序的差异清单。

    ``content`` / ``added`` / ``removed`` / ``kind`` / ``link`` 属内容维度；
    ``mtime`` 只影响快照维度（内容摘要不变）。
    """
    before = {entry.relpath: entry for entry in recorded.entries}
    after = {entry.relpath: entry for entry in current.entries}

    changes: list[ManifestChange] = []
    for relpath in sorted(set(before) | set(after), key=lambda item: item.encode("utf-8")):
        old = before.get(relpath)
        new = after.get(relpath)
        if old is None and new is not None:
            changes.append(ManifestChange(relpath=relpath, kind="added"))
            continue
        if new is None and old is not None:
            changes.append(ManifestChange(relpath=relpath, kind="removed"))
            continue
        assert old is not None and new is not None
        if old.kind != new.kind:
            changes.append(ManifestChange(relpath=relpath, kind="kind"))
            continue
        if new.kind == "f":
            if old.digest != new.digest:
                changes.append(ManifestChange(relpath=relpath, kind="content"))
            elif old.mtime_ns != new.mtime_ns:
                changes.append(ManifestChange(relpath=relpath, kind="mtime"))
            continue
        if new.kind == "l" and old.target != new.target:
            changes.append(ManifestChange(relpath=relpath, kind="link"))
    return tuple(changes)
