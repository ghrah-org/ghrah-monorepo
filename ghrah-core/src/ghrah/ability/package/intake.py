# SPDX-FileCopyrightText: 2026 chenxya <chenxya@ghrah.org>
#
# SPDX-License-Identifier: Apache-2.0
"""上传包入库前的规范化：先扫盘提取成员，再按标准格式重写 ``ability.yaml``。

上传的压缩包允许「不完整」：可以没有 ``ability.yaml``、可以有但缺 ``name``、
``members`` 也可以与磁盘对不上。这里的原则是**先建后验**：

1. 头段能读就用它的身份（``namespace`` / ``name`` / ``version`` / ``title`` /
   ``description``）；读不出来就按**根目录名**合成一个；
2. 成员**一律以磁盘为准**：``operations/*.py`` 与 ``skills/<name>/SKILL.md``
   扫出来即为成员，旧的 ``members`` 不参与；
3. 渲染成标准 v2 文本并就地写回 ``ability.yaml``，随后才交给安装器与 ABI 预检。

真正的安装失败只剩下「装不进去」：既没有 ``operations/`` 也没有 ``skills/``、
目录树不可移植、命名空间/成员名保留字、以及成员自身不合格。
"""

from __future__ import annotations

import re
from collections.abc import Mapping
from dataclasses import dataclass
from pathlib import Path
from typing import Any

import yaml

from ghrah.ability.loading.ability_schema import (
    V2_SCHEMA_VERSION,
    AbilityDefinition,
    load_yaml,
    parse_definition,
)
from ghrah.ability.package.generate import (
    BuildError,
    header_from_text,
    render_document,
    write_definition,
)

__all__ = ["IntakeError", "IntakeResult", "normalize_source"]

_DEFINITION_NAME = "ability.yaml"
_FORMAT = "ghrah.ability-definition"
_DEFAULT_VERSION = "0.1.0"
_SLUG = re.compile(r"[^a-z0-9_-]+")
_VERSION_SUFFIX = re.compile(r"[-_]v?\d+\.\d+\.\d+$")


class IntakeError(ValueError):
    """源包无法规范化成可安装的能力包；``code`` 供接口层映射。"""

    def __init__(self, code: str, message: str) -> None:
        super().__init__(message)
        self.code = code
        self.message = message


@dataclass(frozen=True)
class IntakeResult:
    """一次规范化结果：磁盘上的 ``ability.yaml`` 已按标准格式写好。

    ``is_mcp`` 为真时**不规范化**：MCP 声明的成员来自远端服务器，安装器会用
    ``install_mcp`` 现场观测并写快照，扫盘与 ABI 预检都不适用。
    """

    definition: AbilityDefinition | None
    rendered: str
    changed: bool
    warnings: tuple[str, ...] = ()
    is_mcp: bool = False


def normalize_source(source: str | Path, *, name_hint: str | None = None) -> IntakeResult:
    """扫盘提取成员并把标准 ``ability.yaml`` 写回源包根目录。

    ``origin: mcp`` 的声明原样放行：它只有 ``ability.yaml``（不含 operations/skills），
    成员由 ``install_mcp`` 连服务器现场生成。

    Args:
        source: 解包后的源包根目录。
        name_hint: 目录名不足以表达身份时的兜底名字（上传文件名）；仅在合成
            头段时使用，已有合法 ``ability.yaml`` 时忽略。

    Returns:
        规范化结果；``definition`` 是渲染文本重新解析出来的运行时定义
        （MCP 声明为 ``None``，以 ``is_mcp`` 标记）。

    Raises:
        IntakeError: 目录树里既没有 ``operations/`` 也没有 ``skills/``
            （不是能力包），或渲染出的定义不合格。
    """
    root = Path(source)
    raw = _read_text(root / _DEFINITION_NAME)
    if raw is not None and _declares_mcp(raw):
        return IntakeResult(definition=None, rendered=raw, changed=False, is_mcp=True)
    header, warnings = _header_for(root, name_hint=name_hint)
    try:
        rendered, _members, _warnings = render_document(root, header, lenient=True)
    except BuildError as exc:
        message = str(exc)
        if "至少要有一个成员" in message:
            raise IntakeError(
                "not_a_package",
                "不是能力包：既没有 operations/ 下的 .py，也没有 skills/<name>/SKILL.md",
            ) from exc
        raise IntakeError("invalid_package", message) from exc
    previous = _read_text(root / _DEFINITION_NAME)
    write_definition(root, rendered)
    try:
        definition = parse_definition(rendered, source=str(root))
    except Exception as exc:  # noqa: BLE001 — 渲染文本过不了解析器自检
        raise IntakeError("invalid_package", f"生成的 ability.yaml 不合格: {exc}") from exc
    return IntakeResult(
        definition=definition,
        rendered=rendered,
        changed=previous != rendered,
        warnings=warnings,
    )


def _read_text(path: Path) -> str | None:
    try:
        return path.read_text(encoding="utf-8")
    except OSError, UnicodeDecodeError:
        return None


def _declares_mcp(raw: str) -> bool:
    """只 peek 头段的 ``origin``：MCP 声明走 ``install_mcp``，不参与扫盘规范化。"""
    try:
        data = load_yaml(raw)
    except Exception:  # noqa: BLE001 — 头段读不出来按普通包处理（后续会报具体错）
        return False
    return isinstance(data, Mapping) and data.get("origin") == "mcp"


def _header_for(
    root: Path, *, name_hint: str | None = None
) -> tuple[dict[str, Any], tuple[str, ...]]:
    """优先用既有的合法头段；缺失或不可用时按目录名合成一个。"""
    warnings: list[str] = []
    raw = _read_text(root / _DEFINITION_NAME)
    if raw is not None:
        try:
            return header_from_text(raw), tuple(warnings)
        except BuildError as exc:
            warnings.append(f"ability.yaml 头段不可用，按目录名重建：{exc}")
    header = _synthesized_header(root, name_hint=name_hint)
    try:
        header_from_text(yaml.safe_dump(header, allow_unicode=True, sort_keys=False))
    except BuildError as exc:  # 合成的身份不合法：交给调用方报明确的错
        raise IntakeError("invalid_package", f"无法从目录名推导包身份: {exc}") from exc
    return header, tuple(warnings)


def _synthesized_header(root: Path, *, name_hint: str | None = None) -> dict[str, Any]:
    """按根目录名（或上传文件名）合成头段：``acme.tools`` → ``acme`` / ``tools``。"""
    namespace, name = _identity_from_directory(name_hint or root.name)
    identity: dict[str, Any] = {"namespace": namespace, "version": _DEFAULT_VERSION}
    if name is not None:
        identity["name"] = name
    title = (name or namespace).replace("-", " ").replace("_", " ").title()
    return {
        "format": _FORMAT,
        "schema_version": V2_SCHEMA_VERSION,
        "identity": identity,
        "title": title,
        "description": f"{title}（上传导入时按目录内容生成）",
    }


def _identity_from_directory(dirname: str) -> tuple[str, str | None]:
    """目录名（或上传文件名）→ ``(namespace, name)``；单段名字按退化包处理。"""
    stem = dirname.strip()
    if stem.lower().endswith(".zip"):
        stem = stem[:-4]
    base = _VERSION_SUFFIX.sub("", stem)
    parts = [part for part in base.split(".") if part]
    if len(parts) >= 2:
        return _slug(parts[0]), _slug(parts[1])
    return _slug(base), None


def _slug(value: str) -> str:
    """规范成 ``^[a-z][a-z0-9_-]*$``；不合法字符折成 ``-``。"""
    text = _SLUG.sub("-", value.strip().lower()).strip("-_")
    if not text:
        return "package"
    if not text[0].isalpha():
        text = f"pkg-{text}"
    return text
