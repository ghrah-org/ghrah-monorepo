# SPDX-FileCopyrightText: 2026 chenxya <chenxya@ghrah.org>
#
# SPDX-License-Identifier: Apache-2.0
"""core 侧统一 Ability CLI：``python -m ghrah.ability.tools.cli <verb> ...``。

工具链（``ghrah ability``）只解析解释器与转调子进程，命令面与领域行为都在本模块：

- ``build`` 委托 P0 冻结的作者侧入口 :mod:`ghrah.ability.tools.build`，不重复实现；
- store 命令（``install`` / ``list`` / ``show`` / ``verify`` / ``refresh`` /
  ``uninstall``）委托安装域接口 ``AbilityInstaller``（``ghrah.ability.package``）。

与工具链的薄桥契约（见 ``ghrah-toolchain`` 的 ``commands/ability.py``）：

- store 命令必须显式给 ``--root``，且是绝对路径；缺失或相对路径按领域错误处理，
  **绝不**回退到 ``~/.ghrah/ability`` 之类的隐式根。``--root`` 不 ``resolve()``，
  存储根本身是否允许符号链接由安装器判定。
- ``--json`` 时 stdout **恰好一个** JSON 对象：成功
  ``{"schema": 1, "verb": ..., "ok": true, "data": ...}``，失败
  ``{"schema": 1, "verb": ..., "ok": false, "error": {"code": ..., "message": ...}}``；
  人类提示与日志一律写 stderr，不污染 stdout。
- 退出码：``0`` 成功、``1`` 领域/校验失败、``2`` 参数错误；``--json`` 不改变退出码。

``build`` 不提供 ``--json``：它原样委托 ``ghrah.ability.tools.build``，保持 P0 入口
的输出、警告与退出码。模块顶层只 import 标准库，生成器与安装域包层在 handler 内
按需 import，保证 ``importlib.util.find_spec`` 探测与 ``--help`` 轻量。
"""

from __future__ import annotations

import argparse
import json
import sys
import traceback
from collections.abc import Callable, Mapping, Sequence
from dataclasses import fields, is_dataclass
from datetime import date, datetime
from pathlib import Path
from typing import Any, NoReturn

__all__ = ["main"]

SCHEMA_VERSION = 1
"""JSON 信封的 schema 版本；与安装记录 lock 的 schema 无关。"""

EXIT_OK = 0
EXIT_DOMAIN = 1
"""领域/校验失败：包不存在、版本冲突、校验不通过、lock 损坏等。"""
EXIT_USAGE = 2
"""参数错误：未知子命令、缺位置参数、``--all`` 与 ``@version`` 互斥等。"""


class CliError(Exception):
    """一次命令的领域失败（默认退出码 1），带稳定错误码供 --json 消费方分派。"""

    def __init__(
        self,
        code: str,
        message: str,
        *,
        exit_code: int = EXIT_DOMAIN,
        details: Any = None,
    ) -> None:
        super().__init__(message)
        self.code = code
        self.message = message
        self.exit_code = exit_code
        self.details = details


class UsageError(CliError):
    """参数错误（退出码 2）；argparse 的 ``error()`` 也统一抛它。"""

    def __init__(self, message: str) -> None:
        super().__init__("usage_error", message, exit_code=EXIT_USAGE)


class _Parser(argparse.ArgumentParser):
    """把 argparse 的错误从 ``SystemExit(2)`` 改成 :class:`UsageError`。

    这样 :func:`main` 能在 ``--json`` 模式下仍输出恰好一个错误信封，而不是让
    argparse 直接往 stderr 打印 usage 并退出；``--help`` 仍走 argparse 的
    ``SystemExit(0)``，保持标准行为。
    """

    def error(self, message: str) -> NoReturn:
        raise UsageError(message)


# ── 信封输出 ─────────────────────────────────────────────────


def _write_envelope(payload: Mapping[str, Any]) -> None:
    """向 stdout 写恰好一个 JSON 对象（单行 + 换行），不混入任何其他文本。"""
    sys.stdout.write(json.dumps(dict(payload), ensure_ascii=False) + "\n")
    sys.stdout.flush()


def _emit_success(verb: str, data: Any) -> None:
    _write_envelope({"schema": SCHEMA_VERSION, "verb": verb, "ok": True, "data": data})


def _report_error(
    verb: str | None,
    error: CliError,
    *,
    json_mode: bool,
    parser: argparse.ArgumentParser,
) -> int:
    """按模式输出一次失败并返回其退出码；JSON 模式 stdout 只有一个对象。"""
    if json_mode:
        payload: dict[str, Any] = {"code": error.code, "message": error.message}
        if error.details is not None:
            payload["details"] = error.details
        _write_envelope({"schema": SCHEMA_VERSION, "verb": verb, "ok": False, "error": payload})
    elif isinstance(error, UsageError):
        print(parser.format_usage().rstrip(), file=sys.stderr)
        print(f"参数错误：{error.message}", file=sys.stderr)
    else:
        print(f"错误（{error.code}）：{error.message}", file=sys.stderr)
        if error.details is not None:
            print(json.dumps(error.details, ensure_ascii=False, indent=2), file=sys.stderr)
    return error.exit_code


# ── 目标与安装根 ─────────────────────────────────────────────

_VERBS = ("build", "install", "list", "show", "verify", "refresh", "uninstall")


def _guess_verb(argv: Sequence[str]) -> str | None:
    """从原始 argv 尽力猜出 verb，供 argparse 失败时填 JSON 信封的 ``verb`` 字段。"""
    for token in argv:
        if token in _VERBS:
            return token
    for token in argv:
        if not token.startswith("-"):
            return token
    return None


def _parse_target(text: str) -> tuple[str, str | None]:
    """把 ``<full-name>[@<version>]`` 按**最后一个** ``@`` 切成身份与可选版本。

    full-name 保留点分身份（``namespace.name`` 或退化的 ``namespace``），本函数
    只做切分与"写法是否成形"的判断；身份字符集、版本是否为合法 SemVer、包是否
    存在等由安装域负责，从而不制造第二套校验。
    """
    full_name, sep, version = text.rpartition("@")
    if not sep:
        full_name, version = text, ""
    if not full_name or (sep and not version):
        raise UsageError(f"目标写法非法：{text!r}；应为 <full-name> 或 <full-name>@<version>")
    if "@" in full_name:
        raise UsageError(f"目标写法非法：{text!r}；full-name 中不能含 '@'")
    return full_name, (version or None)


def _resolve_root(root_text: str | None) -> Path:
    """解析 store 命令的 ``--root``：必需、绝对路径、不 resolve 符号链接。

    缺失或相对路径按**领域错误**（退出码 1）处理，而不是让 argparse 退出 2：
    契约要求 ``--json`` 的失败也是一个信封，且 core 不假设任何隐式默认根。
    """
    if root_text is None:
        raise CliError(
            "root_required",
            "--root 必须显式提供安装存储根（绝对路径）；store 命令没有隐式默认根",
        )
    path = Path(root_text).expanduser()
    if not path.is_absolute():
        raise CliError("root_not_absolute", f"--root 必须是绝对路径：{root_text!r}")
    return path


# ── 安装域按需 import 与错误转译 ─────────────────────────────


def _installer_module() -> Any:
    """按需 import 安装域包层；缺失时给出可行动的领域错误（不假装成功）。"""
    try:
        from ghrah.ability.package import installer as module
    except ImportError as exc:  # pragma: no cover - P2 落地后不可达
        raise CliError(
            "installer_unavailable",
            f"core 安装域包层不可用（ghrah.ability.package.installer）：{exc}",
        ) from exc
    return module


def _installer_error(module: Any, exc: BaseException) -> CliError:
    """把安装器的 ``InstallerError(code, message)`` 转成 CLI 领域错误。"""
    code = getattr(exc, "code", None) or "installer_error"
    message = getattr(exc, "message", None) or str(exc)
    return CliError(str(code), str(message))


def _invoke(root: Path, action: Callable[[Any], Any]) -> Any:
    """打开 ``AbilityInstaller(root)`` 并执行一次领域动作，统一转译领域错误。"""
    module = _installer_module()
    try:
        installer = module.AbilityInstaller(root)
        return action(installer)
    except module.InstallerError as exc:
        raise _installer_error(module, exc) from exc


# ── 领域对象 → JSON 数据形状 ─────────────────────────────────


def _attr(value: Any, name: str, default: Any = None) -> Any:
    """从映射或对象上取字段（安装域对象与 show 的 dict 两种形状都兼容）。"""
    if isinstance(value, Mapping):
        return value.get(name, default)
    return getattr(value, name, default)


def _text(value: Any) -> str | None:
    """标量转文本：None 保持 None；Path 转宿主路径字符串。"""
    if value is None:
        return None
    if isinstance(value, Path):
        return str(value)
    return str(value)


def _items(value: Any) -> list[Any]:
    """把可选序列/单值归一成列表（None → []，标量 → [标量]）。"""
    if value is None:
        return []
    if isinstance(value, Mapping):
        return [value]
    if isinstance(value, (str, bytes)):
        return [value]
    try:
        return list(value)
    except TypeError:
        return [value]


def _jsonable(value: Any) -> Any:
    """把领域值转成 JSON 可编码值：Path/datetime/Enum/映射/序列/dataclass 递归。"""
    if value is None or isinstance(value, bool | int | float | str):
        return value
    if isinstance(value, Path):
        return str(value)
    if isinstance(value, (datetime, date)):
        return value.isoformat()
    if isinstance(value, Mapping):
        return {str(key): _jsonable(item) for key, item in value.items()}
    if isinstance(value, (list, tuple, set, frozenset)):
        return [_jsonable(item) for item in value]
    if is_dataclass(value) and not isinstance(value, type):
        return {field.name: _jsonable(getattr(value, field.name)) for field in fields(value)}
    enum_value = getattr(value, "value", None)
    if isinstance(enum_value, str | int):
        return enum_value
    return str(value)


def _source_payload(record: Any) -> dict[str, Any] | None:
    """记录上的 ``source`` 段：``{kind, ref}``；缺失时返回 None。"""
    source = _attr(record, "source")
    if source is None:
        return None
    return {"kind": _text(_attr(source, "kind")), "ref": _text(_attr(source, "ref"))}


def _mcp_payload(record: Any) -> dict[str, Any] | None:
    """记录上的 ``mcp`` 段（仅 MCP 包）：``{fetched_at, stale, last_error}``。"""
    mcp = _attr(record, "mcp")
    if mcp is None:
        return None
    payload = {
        "fetched_at": _text(_attr(mcp, "fetched_at")),
        "stale": _attr(mcp, "stale"),
        "last_error": _text(_attr(mcp, "last_error")),
    }
    return _jsonable(payload)


def _lock_state(record: Any) -> str:
    """安装记录状态：优先记录已判定的 ``lock_state``，否则按 lock 是否存在降级推导。"""
    state = _attr(record, "lock_state")
    if state is not None:
        return str(state)
    lock = _attr(record, "lock")
    return "ok" if lock is not None else "missing"


def _version_entry(record: Any, *, fallback_full_name: str | None = None) -> dict[str, Any]:
    """一条版本记录 → JSON 视图（list 与 show 共用）。

    ``InstalledRecord``（list）与 ``show`` 的单版本 dict 都是**顶层**携带
    ``installed_at`` / ``source`` / ``mcp`` / ``lock_state`` 字段，因此这里不读
    嵌套的 lock 对象；对象与映射两种形状由 :func:`_attr` 统一兼容。
    """
    entry = {
        "full_name": _text(_attr(record, "full_name")) or fallback_full_name,
        "version": _text(_attr(record, "version")),
        "root": _text(_attr(record, "root")),
        "installed_at": _text(_attr(record, "installed_at")),
        "source": _source_payload(record),
        "mcp": _mcp_payload(record),
        "lock_state": _lock_state(record),
        "problem": _text(_attr(record, "problem")),
    }
    return entry


def _install_payload(result: Any) -> dict[str, Any]:
    """``install`` 成功数据：``{full_name, version, root, package_digest, already_installed}``。"""
    return {
        "full_name": _text(_attr(result, "full_name")),
        "version": _text(_attr(result, "version")),
        "root": _text(_attr(result, "root")),
        "package_digest": _text(_attr(result, "package_digest")),
        "already_installed": bool(_attr(result, "already_installed", False)),
    }


def _listing_payload(records: Any) -> list[dict[str, Any]]:
    """``list`` 的版本视图列表；兼容"每条记录一个版本"与"每包分组多个版本"两种形状。"""
    entries: list[dict[str, Any]] = []
    for record in _items(records):
        nested = _attr(record, "versions")
        if nested is None:
            entries.append(_version_entry(record))
            continue
        full_name = _text(_attr(record, "full_name"))
        for version_record in _items(nested):
            entries.append(_version_entry(version_record, fallback_full_name=full_name))
    return entries


def _show_entry(item: Any, *, full_name: str) -> dict[str, Any]:
    """``show`` 的单版本视图：在版本记录上补 ``definition``（解析后的包定义）。"""
    entry = _version_entry(item, fallback_full_name=full_name)
    definition = _attr(item, "definition")
    entry["definition"] = _jsonable(definition) if definition is not None else None
    return entry


def _show_payload(full_name: str, shown: Any) -> list[dict[str, Any]]:
    """``show`` 返回值的归一：dict / 记录 / 记录列表 → 版本视图列表。"""
    if isinstance(shown, Mapping):
        return [_show_entry(shown, full_name=full_name)]
    if is_dataclass(shown) and not isinstance(shown, type):
        return [_show_entry(shown, full_name=full_name)]
    return [_show_entry(item, full_name=full_name) for item in _items(shown)]


def _verify_ok(item: Any) -> bool:
    """判定一条校验结果是否通过：优先 ``ok`` 字段，其次看是否带错误码/差异。"""
    ok = _attr(item, "ok")
    if isinstance(ok, bool):
        return ok
    if _attr(item, "code") not in (None, "", "ok"):
        return False
    return not any(_attr(item, name) for name in ("added", "removed", "changed"))


def _verify_payload(item: Any) -> dict[str, Any]:
    """``verify`` 单条结果 → JSON 视图。"""
    payload = {
        "full_name": _text(_attr(item, "full_name")),
        "version": _text(_attr(item, "version")),
        "root": _text(_attr(item, "root")),
        "ok": _verify_ok(item),
        "code": _text(_attr(item, "code")),
        "message": _text(_attr(item, "message")),
        "package_digest": _text(_attr(item, "package_digest")),
        "actual_digest": _text(_attr(item, "actual_digest")),
        "added": _jsonable(_attr(item, "added", [])),
        "removed": _jsonable(_attr(item, "removed", [])),
        "changed": _jsonable(_attr(item, "changed", [])),
    }
    return {key: value for key, value in payload.items() if value is not None}


def _verify_failure_summary(full_name: str, results: list[dict[str, Any]]) -> str:
    """把校验失败压缩成一行人类可读摘要（详细差异在 ``error.details``）。"""
    failed = [result for result in results if not result.get("ok")]
    labels = "、".join(
        f"{result.get('full_name') or full_name}@{result.get('version') or '?'}"
        for result in failed
    )
    return f"校验未通过：{labels}（共 {len(failed)} 个版本）"


def _uninstall_payload(result: Any, full_name: str) -> dict[str, Any]:
    """``uninstall`` 成功数据：``{full_name, removed: [{version, root}]}``。"""
    removed = []
    for item in _items(_attr(result, "removed")):
        removed.append(
            {"version": _text(_attr(item, "version")), "root": _text(_attr(item, "root"))}
        )
    return {"full_name": _text(_attr(result, "full_name")) or full_name, "removed": removed}


def _refresh_payload(result: Any, full_name: str) -> dict[str, Any]:
    """``refresh`` 成功数据（P5 才有）：沿用安装结果形状。"""
    payload = _install_payload(result)
    payload["full_name"] = payload["full_name"] or full_name
    return payload


# ── 人类可读输出 ─────────────────────────────────────────────


def _print_install(payload: Mapping[str, Any]) -> None:
    full_name = payload["full_name"] or "?"
    version = payload["version"] or "?"
    if payload["already_installed"]:
        print(f"已安装（版本已存在且内容一致，未做改动）：{full_name}@{version}")
    else:
        print(f"已安装：{full_name}@{version} -> {payload['root']}")


def _print_list(payload: Mapping[str, Any]) -> None:
    versions = payload["versions"]
    if not versions:
        print(f"{payload['root']}：没有已安装的 Ability 包")
        return
    for entry in versions:
        state = entry["lock_state"]
        installed_at = entry["installed_at"] or "-"
        print(f"{entry['full_name']}@{entry['version']}\t{state}\t{installed_at}\t{entry['root']}")


def _print_show(payload: Mapping[str, Any]) -> None:
    full_name = payload["full_name"]
    for entry in payload["versions"]:
        print(f"{full_name}@{entry['version']}")
        print(f"  根目录：{entry['root']}")
        print(f"  安装时间：{entry['installed_at'] or '未知'}")
        source = entry.get("source")
        if source:
            print(f"  安装来源：{source.get('kind') or '?'}:{source.get('ref') or ''}")
        mcp = entry.get("mcp")
        if mcp:
            stale = "stale" if mcp.get("stale") else "fresh"
            print(f"  MCP 快照：{stale}，最近抓取 {mcp.get('fetched_at') or '未知'}")
            if mcp.get("last_error"):
                print(f"  MCP 最近错误：{mcp['last_error']}")
        print(f"  安装记录：{entry['lock_state']}")
        definition = entry.get("definition")
        if isinstance(definition, Mapping):
            title = definition.get("title")
            if title:
                print(f"  标题：{title}")
            members = definition.get("members") or {}
            if isinstance(members, Mapping):
                operations = members.get("operations") or {}
                skills = members.get("skills") or {}
                print(f"  成员：{len(operations)} 个 Operation、{len(skills)} 个 Skill")


def _print_verify(payload: Mapping[str, Any]) -> None:
    full_name = payload["full_name"]
    for entry in payload["results"]:
        label = f"{entry.get('full_name') or full_name}@{entry.get('version') or '?'}"
        if entry.get("ok"):
            print(f"{label}：ok")
            continue
        code = entry.get("code") or "verify_failed"
        print(f"{label}：失败（{code}）{entry.get('message') or ''}".rstrip())
        for kind, label_text in (("added", "新增"), ("removed", "缺失"), ("changed", "被修改")):
            paths = entry.get(kind) or []
            for path in paths:
                print(f"  {label_text}：{path}")


def _print_uninstall(payload: Mapping[str, Any]) -> None:
    removed = payload["removed"]
    if not removed:
        print(f"已卸载：{payload['full_name']}")
        return
    print(f"已卸载：{payload['full_name']}（{len(removed)} 个版本）")
    for entry in removed:
        print(f"  {entry['version']} -> {entry['root']}")


def _print_refresh(payload: Mapping[str, Any]) -> None:
    print(f"已刷新：{payload['full_name']}@{payload['version']}")


# ── handlers ─────────────────────────────────────────────────


def _cmd_build(args: argparse.Namespace) -> int:
    """``build <dir> [--check] [--builtin|--shipped]``：原样委托 P0 入口。"""
    from ghrah.ability.tools.build import main as build_main

    argv = [str(args.directory)]
    if args.check:
        argv.append("--check")
    if args.builtin:
        argv.append("--builtin")
    if getattr(args, "shipped", False):
        argv.append("--shipped")
    return build_main(argv)


def _cmd_install(args: argparse.Namespace) -> int:
    """``install <dir|archive> --root R``：安装（archive/MCP 的拒绝由安装器判定）。"""
    root = _resolve_root(args.root)
    source = Path(args.source).expanduser()
    result = _invoke(root, lambda installer: installer.install(source))
    payload = _install_payload(result)
    if args.json:
        _emit_success("install", payload)
    else:
        _print_install(payload)
    return EXIT_OK


def _cmd_list(args: argparse.Namespace) -> int:
    """``list --root R``：列出安装存储；root 不存在时返回空列表而不是错误。"""
    root = _resolve_root(args.root)
    records = _invoke(root, lambda installer: installer.list())
    payload = {"root": str(root), "versions": _listing_payload(records)}
    if args.json:
        _emit_success("list", payload)
    else:
        _print_list(payload)
    return EXIT_OK


def _cmd_show(args: argparse.Namespace) -> int:
    """``show <full-name>[@version] --root R``：省略版本时列出该身份的全部版本。"""
    root = _resolve_root(args.root)
    full_name, version = _parse_target(args.target)
    shown = _invoke(root, lambda installer: installer.show(full_name, version))
    payload = {"full_name": full_name, "versions": _show_payload(full_name, shown)}
    if args.json:
        _emit_success("show", payload)
    else:
        _print_show(payload)
    return EXIT_OK


def _cmd_verify(args: argparse.Namespace) -> int:
    """``verify <full-name>[@version] --root R``：任一版本校验失败即退出 1。"""
    root = _resolve_root(args.root)
    full_name, version = _parse_target(args.target)
    results = _invoke(root, lambda installer: installer.verify(full_name, version))
    views = [_verify_payload(item) for item in _items(results)]
    payload = {"full_name": full_name, "results": views}
    failures = [view for view in views if not view.get("ok")]
    if args.json:
        if failures:
            raise CliError(
                "verify_failed",
                _verify_failure_summary(full_name, views),
                details=payload,
            )
        _emit_success("verify", payload)
    else:
        _print_verify(payload)
    return EXIT_DOMAIN if failures else EXIT_OK


def _cmd_refresh(args: argparse.Namespace) -> int:
    """``refresh <full-name> --root R``：刷新 MCP 包的最新候选快照。"""
    root = _resolve_root(args.root)
    full_name, version = _parse_target(args.target)
    if version is not None:
        raise UsageError("参数错误：refresh 不接受 @version（版本由 MCP 快照自行递增）")
    result = _invoke(root, lambda installer: installer.refresh(full_name=full_name))
    payload = _refresh_payload(result, full_name)
    if args.json:
        _emit_success("refresh", payload)
    else:
        _print_refresh(payload)
    return EXIT_OK


def _cmd_uninstall(args: argparse.Namespace) -> int:
    """``uninstall <full-name>[@version] [--all] [--yes] --root R``。

    省略版本且未给 ``--all`` 时不隐式全删：交给安装器报 ``version_required``
    （领域错误，退出码 1）。``--all`` 与 ``@version`` 互斥，按参数错误退出 2。
    """
    full_name, version = _parse_target(args.target)
    if args.all_versions and version is not None:
        raise UsageError(
            "参数错误：--all 表示删除该身份的全部版本，不能再给 @version；"
            "请二选一（如 acme.reporting 或 acme.reporting@1.4.0）"
        )
    root = _resolve_root(args.root)
    result = _invoke(
        root,
        lambda installer: installer.uninstall(
            full_name,
            version,
            all=args.all_versions,
            confirm=args.yes,
        ),
    )
    payload = _uninstall_payload(result, full_name)
    if args.json:
        _emit_success("uninstall", payload)
    else:
        _print_uninstall(payload)
    return EXIT_OK


# ── 命令行装配 ───────────────────────────────────────────────


def _add_store_options(parser: argparse.ArgumentParser) -> None:
    """store 命令共用的输出与存储根选项（``build`` 不接这两项）。"""
    parser.add_argument(
        "--root",
        metavar="<ability-root>",
        help="安装存储根（绝对路径，必需；由工具链拼成 <发行版>/ability）",
    )
    parser.add_argument(
        "--json",
        action="store_true",
        help="stdout 只输出一个 JSON 对象（成功与失败都如此），退出码不变",
    )


def _build_parser() -> argparse.ArgumentParser:
    """装配七个 verb 的命令树；参数形状与工具链转调一致。"""
    parser = _Parser(
        prog="ghrah.ability.tools.cli",
        description="ghrah Ability 统一命令入口（core 侧；通常由 ghrah ability 转调）",
    )
    verbs = parser.add_subparsers(dest="verb", required=True, metavar="<verb>", title="子命令")

    build = verbs.add_parser("build", help="生成并校验 Ability 包的索引（作者侧，不落安装）")
    build.add_argument("directory", metavar="<dir>", type=Path, help="Ability 源包根目录")
    build.add_argument("--check", action="store_true", help="只校验不写盘；索引不一致时退出 1")
    build_origin = build.add_mutually_exclusive_group()
    build_origin.add_argument("--builtin", action="store_true", help="允许内置包的 ghrah 命名空间")
    build_origin.add_argument(
        "--shipped", action="store_true", help="允许发行版预装包的 ghrah 命名空间"
    )
    build.set_defaults(handler=_cmd_build)

    install = verbs.add_parser("install", help="把 Ability 包安装到指定安装存储")
    install.add_argument("source", metavar="<dir|archive>", help="源包目录或包归档文件")
    _add_store_options(install)
    install.set_defaults(handler=_cmd_install)

    listing = verbs.add_parser("list", help="列出安装存储里已安装的 Ability 包")
    _add_store_options(listing)
    listing.set_defaults(handler=_cmd_list)

    for name, help_text in (
        ("show", "查看某个 Ability 包的详情（省略版本时列出该身份的全部版本）"),
        ("verify", "校验某个 Ability 包及安装记录（省略版本时校验该身份的全部版本）"),
        ("refresh", "刷新 MCP 包的工具快照"),
    ):
        sub = verbs.add_parser(name, help=help_text)
        sub.add_argument(
            "target",
            metavar="<full-name>[@<version>]",
            help="包身份（namespace.name 或退化的 namespace）与可选版本",
        )
        _add_store_options(sub)
        sub.set_defaults(
            handler={"show": _cmd_show, "verify": _cmd_verify, "refresh": _cmd_refresh}[name]
        )

    uninstall = verbs.add_parser("uninstall", help="卸载 Ability 包（必须给 @version 或 --all）")
    uninstall.add_argument(
        "target",
        metavar="<full-name>[@<version>]",
        help="包身份（namespace.name 或退化的 namespace）与可选版本",
    )
    uninstall.add_argument(
        "--all",
        dest="all_versions",
        action="store_true",
        help="删除该身份的全部版本（与 @version 互斥）",
    )
    uninstall.add_argument("--yes", action="store_true", help="跳过确认；P2 无交互确认，无副作用")
    _add_store_options(uninstall)
    uninstall.set_defaults(handler=_cmd_uninstall)

    return parser


# ── 入口 ─────────────────────────────────────────────────────


def main(argv: Sequence[str] | None = None) -> int:
    """CLI 入口；返回进程退出码（0 成功 / 1 领域失败 / 2 参数错误）。"""
    args_list = list(sys.argv[1:]) if argv is None else list(argv)
    json_requested = "--json" in args_list
    parser = _build_parser()
    try:
        args = parser.parse_args(args_list)
    except UsageError as error:
        return _report_error(_guess_verb(args_list), error, json_mode=json_requested, parser=parser)
    try:
        return int(args.handler(args))
    except CliError as error:
        return _report_error(
            args.verb, error, json_mode=getattr(args, "json", False), parser=parser
        )
    except KeyboardInterrupt:  # pragma: no cover - 终端把 Ctrl+C 同时发给父子进程
        raise
    except OSError as error:
        failure = CliError("io_error", f"文件系统操作失败：{error}")
        return _report_error(
            args.verb, failure, json_mode=getattr(args, "json", False), parser=parser
        )
    except Exception as error:  # noqa: BLE001 - 契约要求 --json 失败也只输出一个对象
        traceback.print_exc(file=sys.stderr)
        failure = CliError("internal_error", f"未预期的错误：{type(error).__name__}: {error}")
        return _report_error(
            args.verb, failure, json_mode=getattr(args, "json", False), parser=parser
        )
