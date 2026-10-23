# SPDX-FileCopyrightText: 2026 chenxya <chenxya@ghrah.org>
#
# SPDX-License-Identifier: Apache-2.0

"""``ability.lock.yaml`` 的读取与首次生成。

字段归属（见 skill 包规范）：业务侧写 SKILL.md（申请值），框架侧写本文件
（生效值：full_name / version / provenance / 核定后的 permission / protected）。

**首版尚未实现权限核定环节**，因此本文件中的 ``permission`` 是 SKILL.md
申请值的直通；核定（只能加严，例如申请 ``auto`` → 核定 ``hitl``）落地后，
此处改为写核定值。缺 ``permission`` 段时使用保守默认（``approve=hitl``、
``net=deny``、``files`` 为空）。
"""

from __future__ import annotations

import hashlib
from collections.abc import Mapping
from pathlib import Path
from typing import Any

import yaml

from ghrah.skills.errors import SkillLockError
from ghrah.skills.scan import SKILL_FILE_NAME

__all__ = [
    "INITIAL_VERSION",
    "LOCK_FILE_NAME",
    "LOCK_HEADER",
    "LOCK_SCHEMA_VERSION",
    "build_lock",
    "create_lock",
    "extract_permission_request",
    "fingerprint_skill",
    "read_fingerprint",
    "read_lock",
    "render_lock",
]

LOCK_FILE_NAME: str = "ability.lock.yaml"
LOCK_SCHEMA_VERSION: int = 1
INITIAL_VERSION: str = "0.1.0"
"""首次登记时的版本号；后续发布由框架累加（本版未实现累加）。"""

LOCK_HEADER: str = "# ability.lock.yaml —— 框架生成，勿手改\n"

_DEFAULT_APPROVE: str = "hitl"
_DEFAULT_NET: str = "deny"
_PERMISSION_KEY_ORDER = ("approve", "files", "net")


def fingerprint_skill(skill_dir: Path) -> str:
    """内容指纹：SKILL.md 字节的 sha256，带 ``sha256:`` 前缀。"""
    digest = hashlib.sha256((skill_dir / SKILL_FILE_NAME).read_bytes()).hexdigest()
    return f"sha256:{digest}"


def read_fingerprint(lock_path: Path) -> str:
    """读取 lock 里登记的指纹（``provenance.fingerprint``）。

    Raises:
        SkillLockError: lock 不可解析，或其中没有可用的指纹值。
    """
    data = read_lock(lock_path)
    provenance = data.get("provenance")
    if not isinstance(provenance, Mapping):
        raise SkillLockError(f"lock 缺少 provenance 映射: {lock_path}")
    value = provenance.get("fingerprint")
    if not isinstance(value, str) or not value:
        raise SkillLockError(f"lock 缺少 provenance.fingerprint: {lock_path}")
    return value


def extract_permission_request(frontmatter: Mapping[str, Any]) -> dict[str, Any]:
    """从 SKILL.md frontmatter 提取权限申请，按固定键序返回。

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
    fingerprint: str,
    frontmatter: Mapping[str, Any],
    protected: bool = False,
) -> dict[str, Any]:
    """构造 lock 内容（首次登记的初始值）。

    Args:
        full_name: 技能身份 = 包目录名（``<包名>-<技能名>``，无包名时等于技能名）。
        source: 来源标签（目录池的 ``source``）。
        fingerprint: 内容指纹（见 :func:`fingerprint_skill`）。
        frontmatter: SKILL.md frontmatter，用于取权限申请。
        protected: 信任根核定值；自封无效，本版恒为 False。
    """
    return {
        "schema": LOCK_SCHEMA_VERSION,
        "full_name": full_name,
        "version": INITIAL_VERSION,
        "provenance": {"source": source, "fingerprint": fingerprint},
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
    """读取并粗校验 lock 文件。

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
