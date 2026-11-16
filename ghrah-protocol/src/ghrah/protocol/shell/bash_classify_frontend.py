# SPDX-FileCopyrightText: 2026 chenxya <chenxya@ghrah.org>
#
# SPDX-License-Identifier: Apache-2.0

"""bash-classify 前端：结构化解析与规则分级，供 CommandSafetyChecker 使用。

权威规则来自 bash-classify ≥0.14.1 内置 YAML；可选仓内
``bash_classify_commands/*.yaml`` 以 ``extends: builtin`` 增量覆盖。
不读取 ``BASH_CLASSIFY_CONFIG_DIR`` / ``~/.config``。
"""

from __future__ import annotations

import re
import shlex
from functools import lru_cache
from pathlib import Path

import tree_sitter_bash
from bash_classify import Classification, Risk, classify_expression
from bash_classify.database import CommandDatabase, get_default_commands_dir
from bash_classify.models import CommandResult, ExpressionResult, InnerCommandResult
from tree_sitter import Language, Node, Parser

from ghrah.protocol.shell.command_scope import parse_command_entry

__all__ = [
    "OVERRIDES_DIR",
    "classify_command_expression",
    "get_command_database",
    "iter_classified_segments",
    "segment_base_sub",
    "is_readonly_low",
    "expression_is_dangerous",
    "expression_is_unknown",
]

OVERRIDES_DIR = Path(__file__).resolve().parent / "bash_classify_commands"

_Segment = CommandResult | InnerCommandResult
_SHELLS_WITH_C = frozenset({"bash", "sh", "dash"})
_CLUSTERED_SHORT = re.compile(r"^-[A-Za-z]+$")


@lru_cache(maxsize=1)
def get_command_database() -> CommandDatabase:
    """内置规则 + 仓内覆盖目录；进程内缓存一份。"""
    return CommandDatabase(get_default_commands_dir(), OVERRIDES_DIR)


def classify_command_expression(command: str) -> ExpressionResult:
    """对整条 shell 表达式做 bash-classify 分级。

    ``bash`` / ``sh`` / ``dash`` 把 ``-c`` 粘进短选项簇（如 ``-lc``）时，只改写
    送给分类器的 argv，不改调用方即将执行的原字符串。
    """
    return classify_expression(_expand_clustered_shell_c(command), database=get_command_database())


def _expand_clustered_shell_c(command: str) -> str:
    """把含 ``c`` 的粘连短选项拆开，便于 ``-c`` 委托展开。"""
    source = command.encode("utf-8")
    tree = Parser(Language(tree_sitter_bash.language())).parse(source)
    edits: list[tuple[int, int, bytes]] = []

    def visit(node: Node) -> None:
        if node.type == "command":
            name = node.child_by_field_name("name")
            if (
                name is not None
                and Path(source[name.start_byte : name.end_byte].decode()).name in _SHELLS_WITH_C
            ):
                for argument in node.named_children:
                    if argument.start_byte <= name.end_byte:
                        continue
                    token = source[argument.start_byte : argument.end_byte].decode()
                    if not _CLUSTERED_SHORT.fullmatch(token):
                        break
                    if "c" in token[1:]:
                        expanded = " ".join(f"-{letter}" for letter in token[1:]).encode()
                        edits.append((argument.start_byte, argument.end_byte, expanded))
                        break
        for child in node.named_children:
            visit(child)

    visit(tree.root_node)
    for start, end, replacement in sorted(edits, reverse=True):
        source = source[:start] + replacement + source[end:]
    return source.decode("utf-8")


def iter_classified_segments(expression_result: ExpressionResult) -> list[_Segment]:
    """展平顶层调用与委托内层，供逐段危险叠加与范围检查。"""
    segments: list[_Segment] = []

    def walk(node: _Segment) -> None:
        segments.append(node)
        for inner in node.inner_commands or ():
            walk(inner)

    for command in expression_result.commands or ():
        walk(command)
    return segments


def segment_base_sub(argv: list[str] | tuple[str, ...]) -> tuple[str, str | None]:
    """从一段 argv 提取 (base, sub)，复用既有前置选项跳过逻辑。"""
    if not argv:
        return "", None
    parsed = parse_command_entry(shlex.join(list(argv)))
    if parsed.ambiguous and not parsed.base:
        return Path(argv[0]).name.lower(), None
    return parsed.base, parsed.sub


def is_readonly_low(expression_result: ExpressionResult) -> bool:
    """阶段 1 自动放行：READONLY + LOW，无解析告警，且未命中敏感路径。"""
    if expression_result.sensitive_paths:
        return False
    return (
        expression_result.classification is Classification.READONLY
        and expression_result.risk is Risk.LOW
        and not expression_result.parse_warnings
    )


def expression_is_dangerous(expression_result: ExpressionResult) -> bool:
    return expression_result.classification is Classification.DANGEROUS


def expression_is_unknown(expression_result: ExpressionResult) -> bool:
    if expression_result.classification is Classification.UNKNOWN:
        return True
    return any(
        segment.classification is Classification.UNKNOWN
        for segment in iter_classified_segments(expression_result)
    )
