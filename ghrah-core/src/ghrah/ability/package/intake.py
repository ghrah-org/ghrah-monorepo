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

import os
import re
import shutil
import uuid
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
    """一次规范化结果：``root`` 下的 ``ability.yaml`` 已按标准格式写好。

    ``is_mcp`` 为真时**不规范化**：MCP 声明的成员来自远端服务器，安装器会用
    ``install_mcp`` 现场观测并写快照，扫盘与 ABI 预检都不适用。
    """

    definition: AbilityDefinition | None
    rendered: str
    changed: bool
    warnings: tuple[str, ...] = ()
    is_mcp: bool = False
    root: Path | None = None
    """可安装的包根；原始结构不规整时会指向重新组装出来的目录。"""


def normalize_source(source: str | Path, *, name_hint: str | None = None) -> IntakeResult:
    """扫盘提取成员并把标准 ``ability.yaml`` 写回源包根目录。

    **不检查上传包的文件结构**：成员可以在任意层级（``ability/<name>/``、
    ``skills/<name>/``、根目录直接放 ``SKILL.md``…）。只要能发现成员，就把它
    组装成标准布局再安装；结构不规整时会在源目录旁生成一个临时包根。

    ``origin: mcp`` 的声明原样放行：它只有 ``ability.yaml``（不含 operations/skills），
    成员由 ``install_mcp`` 连服务器现场生成。

    Args:
        source: 解包后的源根目录。
        name_hint: 目录名不足以表达身份时的兜底名字（上传文件名）；仅在合成
            头段时使用，已有合法 ``ability.yaml`` 时忽略。

    Returns:
        规范化结果；``root`` 是要安装的包根，``definition`` 是渲染文本重新解析出
        来的运行时定义（MCP 声明为 ``None``，以 ``is_mcp`` 标记）。

    Raises:
        IntakeError: 整个解包树里找不到任何成员，或渲染出的定义不合格。
    """
    root = Path(source)
    raw = _read_text(root / _DEFINITION_NAME)
    if raw is not None and _declares_mcp(raw):
        return IntakeResult(definition=None, rendered=raw, changed=False, is_mcp=True, root=root)
    warnings: tuple[str, ...] = ()
    root, relocated_name, origin = _discover_package(root)
    if origin is not None:
        warnings = (f"成员不在根目录，已按发现结果组装包根：{origin}",)
    # 先把包内文本源统一成 UTF-8 无 BOM + LF：上传包常见的 CRLF/BOM 不该阻断安装，
    # 也不该让 SKILL.md frontmatter 解析失败。这一步只改文本，不动二进制。
    normalized = _normalize_text_sources(root)
    adopted = _adopt_bare_skill(root, name_hint=name_hint)
    header, header_warnings = _header_for(root, name_hint=relocated_name or name_hint)
    warnings = (*warnings, *header_warnings)
    if normalized:
        warnings = (*warnings, f"已把 {len(normalized)} 个文本源统一为 LF")
    if adopted:
        warnings = (*warnings, f"顶层裸技能目录已收纳为 skills/{adopted}/")
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
        root=root,
    )


def _read_text(path: Path) -> str | None:
    try:
        return path.read_text(encoding="utf-8")
    except OSError, UnicodeDecodeError:
        return None


def _member_roots(root: Path) -> list[Path]:
    """在整棵解包树里找「像包根」的目录：有 ability.yaml / SKILL.md / skills / operations。"""
    found: list[Path] = []
    for dirpath, dirnames, filenames in os.walk(root):
        dirnames[:] = [
            name
            for name in dirnames
            if name.casefold() not in _SKIP_DIRS and name.casefold() != "__macosx"
        ]
        here = Path(dirpath)
        has_definition = _DEFINITION_NAME in filenames
        has_skill_body = "SKILL.md" in filenames
        skills_dir = here / "skills"
        has_skills = skills_dir.is_dir() and any(skills_dir.glob("*/SKILL.md"))
        operations = here / "operations"
        has_operations = operations.is_dir() and any(operations.glob("*.py"))
        if has_definition or has_skill_body or has_skills or has_operations:
            found.append(here)
    # 成员目录内部的候选（例如技能目录里的示例 SKILL.md）不算独立包根。
    pruned: list[Path] = []
    for candidate in found:
        nested = any(
            other != candidate
            and (
                candidate.is_relative_to(other / "skills")
                or candidate.is_relative_to(other / "operations")
            )
            for other in found
        )
        if not nested:
            pruned.append(candidate)
    return pruned


def _member_score(candidate: Path) -> int:
    """候选包根的成员数（技能正文 + operations 脚本），用于挑最完整的一个。"""
    skills = sum(1 for _ in candidate.glob("skills/*/SKILL.md"))
    operations = sum(1 for _ in candidate.glob("operations/*.py"))
    bare = 1 if (candidate / "SKILL.md").is_file() else 0
    return skills + operations + bare


def _stage_tree(source: Path, chosen: Path) -> Path:
    """把候选包根整棵复制到源目录旁的临时包根，返回新路径。"""
    staged = source.parent / f".intake-{uuid.uuid4().hex}"
    shutil.copytree(chosen, staged)
    return staged


def _discover_package(root: Path) -> tuple[Path, str | None, str | None]:
    """发现包根；结构不规整时组装一个可安装的包根。

    Returns:
        ``(包根, 合成身份用的名字, 原始相对路径)``。没有搬动时后两项为 ``None``；
        找不到任何候选时原样返回 ``root``，让后续渲染给出「没有成员」的错误。
    """
    candidates = _member_roots(root)
    if not candidates or root in candidates:
        return root, None, None
    with_definition = [c for c in candidates if (c / _DEFINITION_NAME).is_file()]
    if len(candidates) == 1 or with_definition:
        chosen = max(with_definition or candidates, key=_member_score)
        return (
            _stage_tree(root, chosen),
            chosen.name,
            chosen.relative_to(root).as_posix(),
        )
    return _assemble_package(root, candidates), None, None


def _assemble_package(root: Path, candidates: list[Path]) -> Path:
    """把分散在多处的成员组装成一个标准包根。"""
    staged = root.parent / f".intake-{uuid.uuid4().hex}"
    (staged / "skills").mkdir(parents=True, exist_ok=True)
    (staged / "operations").mkdir(parents=True, exist_ok=True)
    for candidate in candidates:
        if (candidate / "SKILL.md").is_file():
            meta = _frontmatter(candidate / "SKILL.md")
            raw_name = meta.get("name")
            source = raw_name if isinstance(raw_name, str) and raw_name.strip() else candidate.name
            target = staged / "skills" / _skill_key(source)
            if target.exists():
                continue
            shutil.copytree(candidate, target)
        elif (candidate / "operations").is_dir():
            for script in candidate.glob("operations/*.py"):
                shutil.copy2(script, staged / "operations" / script.name)
        elif (candidate / "skills").is_dir():
            for skill_dir in candidate.glob("skills/*"):
                destination = staged / "skills" / skill_dir.name
                if skill_dir.is_dir() and not destination.exists():
                    shutil.copytree(skill_dir, destination)
        definition = candidate / _DEFINITION_NAME
        if definition.is_file() and not (staged / _DEFINITION_NAME).exists():
            shutil.copy2(definition, staged / _DEFINITION_NAME)
    return staged


_TEXT_SUFFIXES = frozenset({".py", ".md", ".yaml", ".yml", ".json", ".txt"})
_SKIP_DIRS = frozenset({"__pycache__", ".git", ".mypy_cache", ".pytest_cache", ".ruff_cache"})
_SKILL_KEY = re.compile(r"[^a-z0-9]+")


def _skill_key(value: str) -> str:
    """技能键规范：小写、非字母数字折成 ``-``、去首尾 ``-``。"""
    text = _SKILL_KEY.sub("-", value.strip().lower()).strip("-")
    return text or "skill"


def _frontmatter(path: Path) -> dict[str, Any]:
    """尽力读 ``SKILL.md`` 的 frontmatter；读不出来返回空映射（后续会报具体错）。"""
    text = _read_text(path) or ""
    if not text.startswith("---\n"):
        return {}
    parts = text.split("\n---\n", 1)
    if len(parts) != 2:
        return {}
    try:
        data = load_yaml(parts[0][4:])
    except Exception:  # noqa: BLE001 — 解析失败交给生成器报「frontmatter 非法」
        return {}
    return data if isinstance(data, Mapping) else {}


def _adopt_bare_skill(root: Path, *, name_hint: str | None = None) -> str | None:
    """把「顶层直接是 SKILL.md」的裸技能目录收纳成 ``skills/<key>/``。

    常见于从别处拿到的单技能包：根目录就是技能目录（``SKILL.md`` +
    ``references/`` + ``scripts/`` …）。这类包结构上完全可用，只是不符合
    我们的 ``skills/<key>/`` 布局，导入时就地搬一层即可。

    Returns:
        收纳后的技能键；不是裸技能目录时返回 ``None``。
    """
    if not (root / "SKILL.md").is_file():
        return None
    if (root / "skills").is_dir() or (root / "operations").is_dir():
        return None
    meta = _frontmatter(root / "SKILL.md")
    raw_name = meta.get("name")
    source = raw_name if isinstance(raw_name, str) and raw_name.strip() else name_hint or root.name
    key = _skill_key(source)
    target = root / "skills" / key
    target.mkdir(parents=True, exist_ok=True)
    for entry in list(root.iterdir()):
        if entry.name in {"skills", _DEFINITION_NAME}:
            continue
        destination = target / entry.name
        if destination.exists():  # 极端情况：整目录里已有同名条目，保留原物
            continue
        shutil.move(str(entry), str(destination))
    return key


def _normalize_text_sources(root: Path) -> tuple[str, ...]:
    """把包内文本源统一成 UTF-8 无 BOM + LF，返回被改写的相对路径。

    上传包经常带着 CRLF 或 BOM（``CREDITS.md``、从别人仓库抄来的 ``SKILL.md``）。
    这些不影响语义，却会让「包内文本必须 LF」的既有校验直接拒绝整个包，因此
    在入库规范化阶段就地修正；二进制文件不碰。
    """
    changed: list[str] = []
    for dirpath, dirnames, filenames in os.walk(root):
        dirnames[:] = [name for name in dirnames if name.casefold() not in _SKIP_DIRS]
        for filename in filenames:
            path = Path(dirpath) / filename
            if path.suffix.casefold() not in _TEXT_SUFFIXES:
                continue
            try:
                data = path.read_bytes()
            except OSError:
                continue
            text = data[3:] if data.startswith(b"\xef\xbb\xbf") else data
            if b"\r" in text:
                text = text.replace(b"\r\n", b"\n").replace(b"\r", b"\n")
            if text != data:
                path.write_bytes(text)
                changed.append(path.relative_to(root).as_posix())
    return tuple(changed)


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
