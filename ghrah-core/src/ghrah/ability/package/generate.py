# SPDX-FileCopyrightText: 2026 chenxya <chenxya@ghrah.org>
#
# SPDX-License-Identifier: Apache-2.0
"""生成器原型：读声明 → 生成 ``members`` → 规范化写回 / ``--check`` 校验。

实现面：

- 头段值保留，``members`` 全量生成；头段非法即拒绝，不写半份索引。
- 生成顺序：``operations`` 键升序 → ``skills`` 键升序；skill 内字段固定
  ``title → description → tags → body → assets → references → scripts``；
  集合字段用映射不用列表。
- YAML 落盘参数固定：``safe_dump(..., allow_unicode=True, sort_keys=False,
  default_flow_style=False, width=4096)``，UTF-8 / LF。
- 写回用同目录临时文件 + ``os.replace``；``PermissionError`` 重试 2 次退避。
- 同一输入两次生成字节相同（幂等）；``--check`` 不写盘，差异非零退出并列差异。
- 生成物写回前用运行时解析器自检，避免写出解析器读不了的索引。

不被任何运行路径 import。
"""

from __future__ import annotations

import difflib
import hashlib
import json
import os
import re
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Any

import yaml

from ghrah.ability.loading.ability_schema import (
    V1_SEMVER_PATTERN,
    load_yaml,
    parse_definition,
)
from ghrah.ability.package.introspect import DeclarationError, read_declaration
from ghrah.ability.package.paths import (
    PathRuleError,
    check_relative_path,
    derive_resource_key,
    ensure_portable_tree,
)
from ghrah.ability.package.schema import strip_schema_metadata as _strip_schema_metadata
from ghrah.manifest.errors import ManifestValidationError

__all__ = [
    "BuildError",
    "IndexMismatchError",
    "build",
    "check",
    "render",
]

_DEFINITION_NAME = "ability.yaml"
_OPERATION_KEY_PATTERN = re.compile(r"^[a-z][a-z0-9_]{0,63}$")
_SKILL_KEY_PATTERN = re.compile(r"^[a-z0-9]+(?:-[a-z0-9]+)*$")
_RESERVED_TOOL_NAMES = frozenset({"search_tools", "describe_tool", "execute_tool", "run_script"})
_PERMISSION_BOOL_KEYS = frozenset(
    {
        "require_hitl",
        "fs_read_only",
        "fs_write",
        "net_access",
        "shell_access",
    }
)
_PERMISSION_LIST_KEYS = frozenset(
    {
        "allowed_paths",
        "denied_paths",
        "allowed_commands",
        "denied_commands",
    }
)
_PERMISSION_KEYS = _PERMISSION_BOOL_KEYS | _PERMISSION_LIST_KEYS
_OLD_LAYOUT_TOP_LEVEL = frozenset({"SKILL.md", "skill", "references", "assets", "scripts"})
_MEDIA_TYPES: dict[str, str] = {
    ".md": "text/markdown",
    ".txt": "text/plain",
    ".json": "application/json",
    ".yaml": "application/yaml",
    ".yml": "application/yaml",
    ".py": "text/x-python",
    ".bin": "application/octet-stream",
}
_SKILL_ALLOWED_ENTRIES = frozenset({"SKILL.md", "references", "assets", "scripts"})


class BuildError(ValueError):
    """生成失败；消息面向包作者，可直接展示。"""


class IndexMismatchError(Exception):
    """``--check`` 发现索引与声明不一致；携带逐条差异。"""

    def __init__(self, differences: list[str]) -> None:
        super().__init__("; ".join(differences))
        self.differences = differences


@dataclass(frozen=True)
class BuildResult:
    """一次生成/校验的结果。"""

    rendered: str
    index_digest: str
    changed: bool
    warnings: tuple[str, ...] = ()


def _load_header(raw: str, *, builtin: bool) -> dict[str, Any]:
    """解析并校验 ``ability.yaml`` 头段。"""
    try:
        data = load_yaml(raw)
    except (yaml.YAMLError, ManifestValidationError) as exc:
        raise BuildError(f"ability.yaml 解析失败: {exc}") from exc
    if not isinstance(data, dict):
        raise BuildError("ability.yaml 不是映射")
    unknown = set(data) - {
        "format",
        "schema_version",
        "origin",
        "identity",
        "title",
        "description",
        "mcp",
        "members",
    }
    if unknown:
        raise BuildError(f"头段未知字段 {sorted(unknown)}（契约拒绝未知字段）")
    if data.get("format") != "ghrah.ability-definition":
        raise BuildError("format 必须是 ghrah.ability-definition")
    if str(data.get("schema_version")) != "1" or not isinstance(data.get("schema_version"), str):
        raise BuildError("schema_version 必须是字符串 '1'（注意：YAML 裸 1 不是 '1'）")
    origin = data.get("origin", "source")
    if origin not in {"source", "mcp"}:
        raise BuildError(f"origin 只能是 source 或 mcp: {origin!r}")
    if origin == "mcp":
        raise BuildError("origin: mcp 的包走 refresh/install，build 拒绝")
    if "mcp" in data:
        raise BuildError("origin: source（含缺省）不得出现 mcp 段")
    identity = data.get("identity")
    if not isinstance(identity, dict):
        raise BuildError("identity 必须是映射")
    unknown_identity = set(identity) - {"namespace", "name", "version"}
    if unknown_identity:
        raise BuildError(f"identity 未知字段 {sorted(unknown_identity)}")
    for field in ("namespace", "version"):
        if not isinstance(identity.get(field), str) or not identity.get(field):
            raise BuildError(f"identity.{field} 必须是非空字符串")
    if not re.fullmatch(r"[a-z][a-z0-9_-]*", identity["namespace"]):
        raise BuildError(f"identity.namespace 非法: {identity['namespace']!r}")
    if identity["namespace"] == "mcp":
        raise BuildError("mcp 命名空间保留给 MCP 快照；source 包不得使用")
    if identity["namespace"] == "ghrah" and not builtin:
        raise BuildError("ghrah 命名空间保留给内置包；外部包 build 拒绝（内置包请使用 --builtin）")
    name = identity.get("name")
    if name is not None and not re.fullmatch(r"[a-z][a-z0-9_-]*", name):
        raise BuildError(f"identity.name 非法: {name!r}")
    if not V1_SEMVER_PATTERN.fullmatch(identity["version"]):
        raise BuildError(
            f"identity.version 必须是 SemVer core 三段（每段 1-9 位、无前导零、"
            f"无预发布/构建元数据）: {identity['version']!r}"
        )
    for field in ("title", "description"):
        value = data.get(field)
        if not isinstance(value, str) or not value.strip():
            raise BuildError(f"{field} 必须是非空字符串")
    return data


def _ensure_object_strict(name: str, node: Any, *, what: str) -> None:
    """要求每个 record object 显式 ``additionalProperties: false``。"""
    if isinstance(node, dict):
        if node.get("type") == "object" and "properties" in node:
            if node.get("additionalProperties") is not False:
                raise BuildError(
                    f"{name}: {what} 是 object schema，必须声明 additionalProperties: false"
                    '（Input/Output 模型请使用 model_config = ConfigDict(extra="forbid")）'
                )
        for key, value in node.items():
            _ensure_object_strict(name, value, what=f"{what}.{key}")
    elif isinstance(node, list):
        for index, item in enumerate(node):
            _ensure_object_strict(name, item, what=f"{what}[{index}]")


def _validate_schema(name: str, node: Any, *, what: str) -> dict[str, Any]:
    """object schema 契约：``additionalProperties: false``、外部 ``$ref`` 拒绝。"""
    if not isinstance(node, dict) or node.get("type") != "object":
        raise BuildError(f"{name}: {what} 必须是 type: object 的 schema")
    _reject_external_ref(name, node, what=what)
    _ensure_object_strict(name, node, what=what)
    return node


def _reject_external_ref(name: str, node: Any, *, what: str) -> None:
    if isinstance(node, dict):
        ref = node.get("$ref")
        if isinstance(ref, str) and not ref.startswith("#/$defs/"):
            raise BuildError(f"{name}: {what} 含外部 $ref: {ref!r}")
        for key, value in node.items():
            _reject_external_ref(name, value, what=f"{what}.{key}")
    elif isinstance(node, list):
        for index, item in enumerate(node):
            _reject_external_ref(name, item, what=f"{what}[{index}]")


def _walk_members(
    files: list[tuple[str, Path]],
) -> tuple[dict[str, Path], dict[str, list[tuple[str, Path]]]]:
    """扫 ``operations/`` 与 ``skills/`` 的文件清单，产出成员文件索引。"""
    op_files: dict[str, Path] = {}
    skill_files: dict[str, list[tuple[str, Path]]] = {}
    for rel, _ in files:
        top = rel.split("/", 1)[0]
        if rel == "ability.lock.yaml":
            raise BuildError("ability.lock.yaml 是安装记录，不是源包文件")
        if top in _OLD_LAYOUT_TOP_LEVEL:
            raise BuildError(
                f"旧布局条目拒绝: {rel}（v1 只允许根 ability.yaml、operations/、skills/）"
            )
        if rel in {"operations", "skills"}:
            raise BuildError(f"{rel} 必须是目录，不能是文件")
    for rel, full in files:
        if rel.startswith("operations/"):
            remainder = rel[len("operations/") :]
            if "/" in remainder:
                raise BuildError(f"operations/ 只扫顶层（子目录拒绝）: {rel}")
            if not remainder.endswith(".py"):
                raise BuildError(f"operations/ 下的非 .py 条目拒绝: {rel}")
            stem = remainder[:-3]
            if stem.startswith("_"):
                continue
            op_files[stem] = full
    if any(rel.startswith("skills/") for rel, _ in files):
        for rel, full in files:
            if not rel.startswith("skills/"):
                continue
            parts = rel.split("/")
            if len(parts) < 3:
                raise BuildError(f"skills/ 下必须是 skills/<name>/…: {rel}")
            skill_name = parts[1]
            if not _SKILL_KEY_PATTERN.fullmatch(skill_name) or len(skill_name) > 64:
                raise BuildError(
                    f"skill 目录名非法（须 ^[a-z0-9]+(-[a-z0-9]+)*$、≤64）: {skill_name!r}"
                )
            skill_files.setdefault(skill_name, []).append((rel, full))
    return op_files, skill_files


def _parse_frontmatter(name: str, text: str) -> dict[str, Any]:
    """解析 SKILL.md frontmatter；非法即失败（不猜默认值）。"""
    if not text.startswith("---\n"):
        raise BuildError(f"{name}: SKILL.md 缺少 frontmatter（`---` 头）")
    parts = text.split("\n---\n", 1)
    if len(parts) != 2:
        raise BuildError(f"{name}: frontmatter 未闭合")
    try:
        meta = load_yaml(parts[0][4:])
    except (yaml.YAMLError, ManifestValidationError) as exc:
        raise BuildError(f"{name}: frontmatter 不是合法 YAML: {exc}") from exc
    if not isinstance(meta, dict):
        raise BuildError(f"{name}: frontmatter 必须是映射")
    return meta


def _skill_member(name: str, entries: list[tuple[str, Path]]) -> dict[str, Any]:
    """从 skill 目录的文件清单生成一个 skill 成员。"""
    body_rel = f"skills/{name}/SKILL.md"
    body_entry = next(((rel, full) for rel, full in entries if rel == body_rel), None)
    if body_entry is None:
        raise BuildError(f"skills/{name}/ 缺少 SKILL.md")
    for rel, _ in entries:
        parts = rel.split("/")
        if len(parts) == 3 and parts[2] != "SKILL.md" and parts[2] not in _SKILL_ALLOWED_ENTRIES:
            raise BuildError(f"skills/{name}/ 下只允许 SKILL.md/references/assets/scripts: {rel}")
        if len(parts) >= 4 and parts[2] not in ("references", "assets", "scripts"):
            raise BuildError(f"skills/{name}/ 下未知子目录: {rel}")
    text = body_entry[1].read_text(encoding="utf-8")
    meta = _parse_frontmatter(f"skills/{name}", text)
    if meta.get("name") != name:
        raise BuildError(
            f"skills/{name}/SKILL.md 的 frontmatter name 是 {meta.get('name')!r}，与目录名不一致"
        )
    description = meta.get("description")
    if not isinstance(description, str) or not description.strip() or len(description) > 1024:
        raise BuildError(f"skills/{name}: description 必须是非空字符串且 ≤1024")
    metadata = meta.get("metadata") or {}
    if not isinstance(metadata, dict):
        raise BuildError(f"skills/{name}: metadata 必须是映射")
    title = metadata.get("title") or name
    if not isinstance(title, str) or not title.strip():
        raise BuildError(f"skills/{name}: metadata.title 必须是非空字符串")
    tags = metadata.get("tags") or []
    if not isinstance(tags, list) or not all(isinstance(t, str) for t in tags):
        raise BuildError(f"skills/{name}: metadata.tags 必须是 list[str]")
    member: dict[str, Any] = {
        "title": title,
        "description": description,
        "tags": sorted(set(tags)),
        "body": {"path": body_rel, "media_type": "text/markdown"},
        "assets": {},
        "references": {},
        "scripts": {},
    }
    _attach_resources(member, name, entries)
    return member


def _attach_resources(member: dict[str, Any], name: str, entries: list[tuple[str, Path]]) -> None:
    """登记 ``assets`` / ``references`` / ``scripts``（路径、媒体类型、size、digest）。"""
    prefix = f"skills/{name}/"
    for role in ("assets", "references", "scripts"):
        table: dict[str, Any] = {}
        for rel, full in entries:
            role_prefix = f"{prefix}{role}/"
            if not rel.startswith(role_prefix):
                continue
            rel_in_role = rel[len(role_prefix) :]
            key = derive_resource_key(rel_in_role)
            if not key:
                raise BuildError(f"skills/{name}/{role}: 资源键为空: {rel}")
            if key in table:
                raise BuildError(f"skills/{name}/{role}: 资源键冲突 {key!r}")
            if role == "scripts" and not rel.endswith(".py"):
                raise BuildError(f"脚本只允许 .py: {rel}")
            suffix = full.suffix.lower()
            data = full.read_bytes()
            table[key] = {
                "path": check_relative_path(rel, what=f"members.skills.{name}.{role}.{key}"),
                "media_type": _MEDIA_TYPES.get(suffix, "application/octet-stream"),
                "size": len(data),
                "digest": f"sha256:{hashlib.sha256(data).hexdigest()}",
            }
        member[role] = dict(sorted(table.items()))


def _validate_permissions(key: str, permissions: dict[str, Any]) -> None:
    """校验包声明的权限申请值；只做形状与类型，不做授权判定。"""
    unknown = set(permissions) - _PERMISSION_KEYS
    if unknown:
        raise BuildError(f"operations/{key}.py: PERMISSIONS 未知键 {sorted(unknown)}")
    for name, value in permissions.items():
        if name in _PERMISSION_BOOL_KEYS and not isinstance(value, bool):
            raise BuildError(f"operations/{key}.py: permissions.{name} 必须是 bool")
        if name in _PERMISSION_LIST_KEYS:
            if not isinstance(value, list) or not all(isinstance(item, str) for item in value):
                raise BuildError(f"operations/{key}.py: permissions.{name} 必须是 list[str]")
    if permissions.get("fs_read_only") and permissions.get("fs_write"):
        raise BuildError(f"operations/{key}.py: fs_read_only 与 fs_write 不得同时为 true")
    for path_field in ("allowed_paths", "denied_paths"):
        for value in permissions.get(path_field, []):
            if value == ".":
                continue
            if (
                not value
                or value.startswith(("/", "\\", "~"))
                or re.match(r"^[A-Za-z]:", value)
                or "\\" in value
                or ".." in value.split("/")
            ):
                raise BuildError(
                    f"operations/{key}.py: permissions.{path_field} 只写相对路径: {value!r}"
                )


def _operation_member(key: str, script: Path, warnings: list[str]) -> dict[str, Any]:
    """从实现模块声明生成一个 operation 成员。"""
    try:
        decl = read_declaration(script)
    except DeclarationError as exc:
        raise BuildError(f"operations/{key}.py: {exc}") from exc
    _validate_permissions(key, decl.permissions)
    input_schema = _strip_schema_metadata(
        _validate_schema(key, decl.input_schema or {}, what="Input.model_json_schema()")
    )
    # 每次新建字面量：同一个字典对象被多个成员复用时，safe_dump 会写
    # ``&id001`` / ``*id001`` 锚点，违反“生成物展开”的冻结规则。
    output_schema: dict[str, Any] = {
        "type": "object",
        "properties": {},
        "additionalProperties": False,
    }
    if decl.output_schema is not None:
        output_schema = _strip_schema_metadata(
            _validate_schema(key, decl.output_schema, what="Output.model_json_schema()")
        )
    else:
        warnings.append(
            f"operations/{key}.py: 缺 Output 模型，output_schema 占位空对象 schema"
            "（P4 严格模式将失败）"
        )
    resources = dict(decl.resources)
    paths = resources.get("paths", [])
    if not isinstance(paths, list) or not all(isinstance(item, str) for item in paths):
        raise BuildError(f"operations/{key}.py: resources.paths 必须是 list[str]")
    properties = input_schema.get("properties", {})
    for param in paths:
        if param not in properties:
            raise BuildError(
                f"operations/{key}.py: resources.paths 引用的参数 {param!r} "
                "不在 input_schema.properties"
            )
    return {
        "title": decl.title,
        "description": decl.summary,
        "entry": f"operations/{key}.py",
        "input_schema": input_schema,
        "output_schema": output_schema,
        "tags": decl.tags,
        "effects": decl.effects,
        "permissions": decl.permissions,
        "resources": resources,
        "execution": {
            "mode": "request",
            "timeout_ms": decl.timeout_ms,
            "cancellation": "unsupported",
            "on_revoke": "drain",
            "retry": "never",
        },
        "expose": decl.expose,
    }


def _render(root: Path, *, builtin: bool) -> tuple[str, dict[str, Any], list[str]]:
    definition_path = root / _DEFINITION_NAME
    if not definition_path.is_file():
        raise BuildError(f"{root}: 缺少 {_DEFINITION_NAME}")
    raw = definition_path.read_text(encoding="utf-8")
    if raw.startswith("\ufeff"):
        raise BuildError("ability.yaml 含 BOM（须 UTF-8 无 BOM）")
    if "\r" in raw:
        raise BuildError("ability.yaml 含 CR/CRLF（须 LF 换行）")
    header = _load_header(raw, builtin=builtin)
    files = ensure_portable_tree(root)
    op_files, skill_files = _walk_members(files)
    operations: dict[str, Any] = {}
    warnings: list[str] = []
    for key in sorted(op_files):
        if not _OPERATION_KEY_PATTERN.fullmatch(key):
            raise BuildError(f"operations/ 成员键非法（^a-z[a-z0-9_]{{0,63}}$）: {key!r}")
        if key in _RESERVED_TOOL_NAMES:
            raise BuildError(f"operations/{key}.py: 保留名（生成期拒绝一次，表面投影再拒绝一次）")
        operations[key] = _operation_member(key, op_files[key], warnings)
    skills: dict[str, Any] = {}
    for name in sorted(skill_files):
        skills[name] = _skill_member(name, skill_files[name])
    if not operations and not skills:
        raise BuildError("operations 与 skills 至少要有一个成员（允许空映射，但合计不能为零）")
    members = {"operations": operations, "skills": skills}
    document = dict(header)
    document.pop("members", None)
    document["members"] = members
    rendered = yaml.safe_dump(
        document, allow_unicode=True, sort_keys=False, default_flow_style=False, width=4096
    )
    try:
        load_yaml(rendered)
        parse_definition(rendered, source=str(root))
    except (yaml.YAMLError, ManifestValidationError) as exc:
        raise BuildError(f"生成的索引未通过解析器自检: {exc}") from exc
    return rendered, members, warnings


def render(root: Path, *, builtin: bool = False) -> tuple[str, dict[str, Any], list[str]]:
    """读声明并渲染完整 ``ability.yaml`` 文本（不写盘）。"""
    try:
        return _render(root, builtin=builtin)
    except PathRuleError as exc:
        raise BuildError(str(exc)) from exc


def _index_digest(members: dict[str, Any]) -> str:
    """规范化 JSON 的 sha256，``sha256:`` 前缀。"""
    blob = json.dumps(members, sort_keys=True, separators=(",", ":"), ensure_ascii=False)
    return f"sha256:{hashlib.sha256(blob.encode('utf-8')).hexdigest()}"


def _write_atomic(path: Path, text: str) -> None:
    """同目录临时文件 + ``os.replace``；``PermissionError`` 重试 2 次退避。"""
    tmp = path.with_name(f".{path.name}.tmp")
    for attempt in range(3):
        try:
            with open(tmp, "w", encoding="utf-8", newline="\n") as stream:
                stream.write(text)
            os.replace(tmp, path)
            return
        except PermissionError as exc:
            if attempt == 2:
                raise BuildError(
                    f"无法写入 {path}: 文件被占用或无权限；请关闭编辑器/杀软后重试"
                ) from exc
            time.sleep(0.2 * (attempt + 1))
        finally:
            if tmp.exists():
                tmp.unlink(missing_ok=True)


def build(root: Path, *, builtin: bool = False) -> BuildResult:
    """生成并写回 ``ability.yaml`` 的 ``members``。"""
    rendered, members, warnings = render(root, builtin=builtin)
    path = root / _DEFINITION_NAME
    changed = path.read_text(encoding="utf-8") != rendered
    if changed:
        _write_atomic(path, rendered)
    return BuildResult(
        rendered=rendered,
        index_digest=_index_digest(members),
        changed=changed,
        warnings=tuple(warnings),
    )


def check(root: Path, *, builtin: bool = False) -> BuildResult:
    """``--check``：不写盘，索引与声明不一致时抛 :class:`IndexMismatchError`。"""
    rendered, members, warnings = render(root, builtin=builtin)
    path = root / _DEFINITION_NAME
    current = path.read_text(encoding="utf-8")
    if current != rendered:
        diff = list(
            difflib.unified_diff(
                current.splitlines(),
                rendered.splitlines(),
                fromfile="当前 ability.yaml",
                tofile="重新生成的 ability.yaml",
                lineterm="",
            )
        )
        raise IndexMismatchError(differences=diff or ["ability.yaml 与重新生成的结果不一致"])
    return BuildResult(
        rendered=rendered,
        index_digest=_index_digest(members),
        changed=False,
        warnings=tuple(warnings),
    )
