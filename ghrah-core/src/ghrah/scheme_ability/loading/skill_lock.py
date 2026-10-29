# SPDX-FileCopyrightText: 2026 chenxya <chenxya@ghrah.org>
#
# SPDX-License-Identifier: Apache-2.0

"""``ability.lock.yaml`` 的读取与首次生成。

字段归属（见 skill 包规范）：业务侧写正文（申请值）+ ``ability.yaml``（身份与版本），
框架侧写本文件（生效值：full_name / provenance / 核定后的 permission / protected）。
**版本不在本文件**：它是定义面（``ability.yaml`` 的 ``identity.version``）的事实。

``schema: 2`` 起，登记的是**整树观测**（见 :mod:`ghrah.scheme_ability.loading.skill_fingerprint`）：
``manifest`` 是权威观测文本，``provenance`` 里的两枚摘要都是它的派生投影——
``content_digest`` 不含 mtime（跨机器 / 跨重装可复现），``snapshot_digest`` 含
mtime（本机状态）。旧 ``schema: 1`` 的 lock 只有单文件指纹，**不自动改写**：
读取时明确报"需迁移"，重建走 ``SkillAbilityProvider.relock()``（零隐式行为）。

**首版尚未实现权限核定环节**，因此本文件中的 ``permission`` 是正文申请值的直通；
核定（只能加严，例如申请 ``auto`` → 核定 ``hitl``）落地后，此处改为写核定值。
缺 ``permission`` 段时使用保守默认（``approve=hitl``、``net=deny``、``files`` 为空）。
"""

from __future__ import annotations

import os
from collections.abc import Mapping
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

import yaml

from ghrah.scheme_ability.loading.ability_schema import LOCK_FILE_NAME
from ghrah.scheme_ability.loading.skill_errors import SkillLockError
from ghrah.scheme_ability.loading.skill_fingerprint import (
    CONTENT_ALGORITHM,
    ENTRY_BODY_RELPATH,
    BundleManifest,
    ManifestDegrade,
    entry_digest,
    parse_degrade,
    parse_manifest,
)
from ghrah.scheme_ability.loading.skill_models import LockRecord

__all__ = [
    "LOCK_FILE_NAME",
    "LOCK_HEADER",
    "LOCK_SCHEMA_VERSION",
    "build_lock",
    "create_lock",
    "extract_permission_request",
    "read_lock",
    "read_lock_record",
    "render_lock",
    "replace_lock",
    "utc_now_rfc3339",
]

LOCK_SCHEMA_VERSION: int = 2

LOCK_HEADER: str = "# ability.lock.yaml —— 框架生成，勿手改\n"

_DEFAULT_APPROVE: str = "hitl"
_DEFAULT_NET: str = "deny"
_PERMISSION_KEY_ORDER = ("approve", "files", "net")


def utc_now_rfc3339() -> str:
    """当前 UTC 时刻的 RFC 3339 文本（秒精度，``Z`` 结尾）。

    落盘时 PyYAML 会给它加引号，读回仍是字符串（不会变成 ``datetime``）——
    这一点由 :mod:`ghrah.scheme_ability.loading.skill_lock` 的往返测试钉住。
    """
    return datetime.now(UTC).isoformat(timespec="seconds").replace("+00:00", "Z")


def extract_permission_request(frontmatter: Mapping[str, Any]) -> dict[str, Any]:
    """从正文申请值提取 permission，按固定键序返回。

    - ``approve`` / ``files`` / ``net`` 缺失或类型非法时填保守默认；
    - 申请里的其余键（如未来的 ``shell``）**原样保留**，不静默丢弃。
    """
    raw = frontmatter.get("permission")
    request: dict[str, Any] = dict(raw) if isinstance(raw, Mapping) else {}

    approve = request.get("approve")
    files = request.get("files")
    net = request.get("net")

    result: dict[str, Any] = {
        "approve": approve if isinstance(approve, str) and approve else _DEFAULT_APPROVE,
        "files": dict(files) if isinstance(files, Mapping) else {},
        "net": net if isinstance(net, str) and net else _DEFAULT_NET,
    }
    for key, value in request.items():
        if key not in result:
            result[key] = value
    return result


def build_lock(
    *,
    full_name: str,
    source: str,
    manifest: BundleManifest,
    frontmatter: Mapping[str, Any],
    recorded_at: str,
    protected: bool = False,
) -> dict[str, Any]:
    """构造 lock 内容（首次登记的初始值）。

    Args:
        full_name: 身份全名（新格式来自 ``ability.yaml`` 的 ``identity``）。
        source: 来源标签（目录池的 ``source``）。
        manifest: 整树观测（见
            :func:`ghrah.scheme_ability.loading.skill_fingerprint.build_manifest`）；
            降级时两枚摘要为 ``null``，绝不写"部分树的摘要"。
        frontmatter: 正文申请值，用于取 permission。
        recorded_at: 登记时刻（RFC 3339，见 :func:`utc_now_rfc3339`）；**不参与摘要**。
        protected: 信任根核定值；自封无效，本版恒为 False。
    """
    return {
        "schema": LOCK_SCHEMA_VERSION,
        "full_name": full_name,
        "provenance": {
            "source": source,
            "algorithm": CONTENT_ALGORITHM,
            "content_digest": manifest.content_digest,
            "snapshot_digest": manifest.snapshot_digest,
            "recorded_at": recorded_at,
        },
        "manifest": {
            "text": manifest.text,
            "degraded": manifest.degraded.to_mapping() if manifest.degraded else None,
            "issues": list(manifest.issues),
        },
        "permission": extract_permission_request(frontmatter),
        "protected": protected,
    }


def render_lock(data: Mapping[str, Any]) -> str:
    """渲染落盘文本：头部注释 + YAML（保持键序、不转义中文）。"""
    body = yaml.safe_dump(
        dict(data),
        sort_keys=False,
        allow_unicode=True,
        default_flow_style=False,
    )
    return LOCK_HEADER + body


def read_lock(path: Path) -> dict[str, Any]:
    """读取并粗校验 lock 文件（不校验 schema / 字段完整性）。

    Raises:
        SkillLockError: 文件不可读、YAML 非法、或顶层不是映射。
    """
    try:
        raw = path.read_text(encoding="utf-8")
    except OSError as exc:
        raise SkillLockError(f"lock 读取失败: {exc}") from exc
    try:
        data = yaml.safe_load(raw)
    except yaml.YAMLError as exc:
        raise SkillLockError(f"lock YAML 解析失败: {exc}") from exc
    if not isinstance(data, Mapping):
        raise SkillLockError("lock 顶层不是 YAML 映射")
    return dict(data)


def read_lock_record(lock_path: Path, *, body_relpath: str = ENTRY_BODY_RELPATH) -> LockRecord:
    """严格读取 lock：校验 schema 与字段，重建登记记录（含整树 manifest）。

    Args:
        lock_path: lock 文件路径。
        body_relpath: 定义面声明的正文路径（``/`` 分隔），用于从登记的整树观测里
            复算入口哨兵；默认取本版约定，与 :func:`fingerprint_entry` 同口径。

    Returns:
        重建出的 :class:`LockRecord`。

    Raises:
        SkillLockError: 不可解析、schema 不符（含旧 schema 需迁移）、字段缺失或
            类型非法、摘要与降级状态自相矛盾、manifest 文本损坏。
    """
    data = read_lock(lock_path)

    schema = data.get("schema")
    if schema != LOCK_SCHEMA_VERSION:
        raise SkillLockError(
            f"lock schema={schema!r} 与当前 {LOCK_SCHEMA_VERSION} 不符"
            f"（需显式重建，不自动改写）: {lock_path}"
        )

    full_name = data.get("full_name")
    if not isinstance(full_name, str) or not full_name:
        raise SkillLockError(f"lock 缺少 full_name: {lock_path}")

    provenance = data.get("provenance")
    if not isinstance(provenance, Mapping):
        raise SkillLockError(f"lock 缺少 provenance 映射: {lock_path}")
    source = provenance.get("source")
    algorithm = provenance.get("algorithm")
    content_digest = provenance.get("content_digest")
    snapshot_digest = provenance.get("snapshot_digest")
    recorded_at = provenance.get("recorded_at")
    if not isinstance(source, str) or not source:
        raise SkillLockError(f"lock 缺少 provenance.source: {lock_path}")
    if not isinstance(algorithm, str) or not algorithm:
        raise SkillLockError(f"lock 缺少 provenance.algorithm: {lock_path}")
    if recorded_at is not None and not isinstance(recorded_at, str):
        raise SkillLockError(f"lock 的 provenance.recorded_at 不是字符串: {lock_path}")

    block = data.get("manifest")
    if not isinstance(block, Mapping):
        raise SkillLockError(f"lock 缺少 manifest 映射: {lock_path}")
    degraded = parse_degrade(block.get("degraded"))

    if degraded is None:
        if content_digest is None or snapshot_digest is None:
            raise SkillLockError(f"lock 未标记降级但摘要缺失: {lock_path}")
    elif content_digest is not None or snapshot_digest is not None:
        raise SkillLockError(f"lock 标记降级却写了摘要（拒绝半可信值）: {lock_path}")
    for label, value in (("content_digest", content_digest), ("snapshot_digest", snapshot_digest)):
        if value is not None and (not isinstance(value, str) or not value):
            raise SkillLockError(f"lock 的 provenance.{label} 非字符串: {lock_path}")

    manifest = _read_manifest_block(block, full_name=full_name, degraded=degraded, path=lock_path)
    return LockRecord(
        schema=LOCK_SCHEMA_VERSION,
        full_name=full_name,
        source=source,
        algorithm=algorithm,
        content_digest=content_digest,
        snapshot_digest=snapshot_digest,
        recorded_at=recorded_at,
        manifest=manifest,
        entry=entry_digest(manifest, body_relpath=body_relpath),
    )


def _read_manifest_block(
    block: Mapping[str, Any],
    *,
    full_name: str,
    degraded: ManifestDegrade | None,
    path: Path,
) -> BundleManifest:
    """从 lock 的 ``manifest`` 段重建 :class:`BundleManifest`。"""
    issues_raw = block.get("issues")
    issues: tuple[str, ...] = ()
    if isinstance(issues_raw, list):
        issues = tuple(str(item) for item in issues_raw)

    if degraded is not None:
        return BundleManifest(full_name=full_name, degraded=degraded, issues=issues)

    text = block.get("text")
    if not isinstance(text, str) or not text:
        raise SkillLockError(f"lock 缺少 manifest.text: {path}")
    manifest_name, entries = parse_manifest(text)
    return BundleManifest(full_name=manifest_name, entries=entries, degraded=None, issues=issues)


def create_lock(path: Path, data: Mapping[str, Any]) -> bool:
    """**仅创建、不覆盖**：文件已存在则返回 False。

    用 ``open(path, "x")`` 独占创建，避免并发下覆盖既有 lock。
    """
    try:
        with path.open("x", encoding="utf-8", newline="\n") as handle:
            handle.write(render_lock(data))
    except FileExistsError:
        return False
    except OSError as exc:
        raise SkillLockError(f"lock 写入失败: {exc}") from exc
    return True


def replace_lock(path: Path, data: Mapping[str, Any]) -> None:
    """**显式重建**：覆盖既有 lock（唯一允许覆盖的路径，见 ``provider.relock``）。

    先写同目录临时文件再 ``os.replace`` 原子替换：中途失败不会留下半个 lock。
    """
    tmp = path.with_name(f"{path.name}.tmp")
    try:
        tmp.write_text(render_lock(data), encoding="utf-8", newline="\n")
        os.replace(tmp, path)
    except OSError as exc:
        raise SkillLockError(f"lock 重建失败: {exc}") from exc
    finally:
        if tmp.exists():
            try:
                tmp.unlink()
            except OSError:
                pass
