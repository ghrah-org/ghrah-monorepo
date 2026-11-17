# SPDX-FileCopyrightText: 2026 chenxya <chenxya@ghrah.org>
#
# SPDX-License-Identifier: Apache-2.0
"""模板 JSON 列的解析策略注册表。

每行有一个 ``format`` 列，决定该行的 ``metadata`` / ``model`` / ``prompt`` /
``context_policy`` / ``ability_request`` / ``permission_request`` / ``path_request``
这七个 JSON 文本**按什么规则**解析与序列化。store 只经本注册表取策略，不硬编码
字段名，因此新增 format 时无需改表、也无需改 store。

首版仅注册 ``ghrah.agent-templates.v1``；未知 format 一律显式失败（fail-closed），
不做「猜一个规则试试」的回退。
"""

from __future__ import annotations

from collections.abc import Mapping
from typing import Any, Protocol, runtime_checkable

from ghrah.subject.template_store.models import AgentTemplateConfig

__all__ = [
    "DEFAULT_FORMAT",
    "TemplateFormat",
    "UnsupportedTemplateFormatError",
    "available",
    "register",
    "resolve",
]

#: 新模板写入时固定使用的 format（调用方不可指定）。
DEFAULT_FORMAT = "ghrah.agent-templates.v1"


class UnsupportedTemplateFormatError(LookupError):
    """行的 ``format`` 没有对应解析策略。"""

    def __init__(self, name: str) -> None:
        self.name = name
        super().__init__(f"不支持的模板 format：{name}")


@runtime_checkable
class TemplateFormat(Protocol):
    """一套 JSON 列的解析/序列化规则。"""

    #: 写入行 ``format`` 列的值。
    name: str

    def to_columns(self, config: AgentTemplateConfig) -> dict[str, str]:
        """配置 → 列文本。"""
        ...

    def from_columns(self, columns: Mapping[str, Any]) -> AgentTemplateConfig:
        """列文本 → 配置。"""
        ...


_REGISTRY: dict[str, TemplateFormat] = {}


def register(fmt: TemplateFormat) -> TemplateFormat:
    """注册一套策略（同名的后注册覆盖先注册，便于测试注入）。"""
    _REGISTRY[fmt.name] = fmt
    return fmt


def resolve(name: str) -> TemplateFormat:
    """取 ``name`` 对应的策略；未注册即报错。"""
    try:
        return _REGISTRY[name]
    except KeyError:
        raise UnsupportedTemplateFormatError(name) from None


def available() -> tuple[str, ...]:
    """已注册的 format 名（诊断与自检用）。"""
    return tuple(sorted(_REGISTRY))


from ghrah.subject.template_store.formats.v1 import V1Format  # noqa: E402

register(V1Format())
