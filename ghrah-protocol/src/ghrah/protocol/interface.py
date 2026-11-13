# SPDX-FileCopyrightText: 2026 chenxya <chenxya@ghrah.org>
#
# SPDX-License-Identifier: Apache-2.0
"""能力清单与收敛点骨架。

与具体模块无关：任何模块都可以直接复用它，自己只提供三样东西——
能力清单实例、请求信封 DTO、错误翻译表。

分层：

- :class:`Interface` —— 一条能力的声明（名字 + 请求信封 + 出参 DTO）；
- :class:`InterfaceRegistry` —— 有序、名字唯一、可枚举的清单；
- :class:`InterfaceRuntime` —— 模块内唯一运行时入口，按序做四件事：
  查表 → 校验入参 → 转交 → 校出参并强制 JSON。
"""

from __future__ import annotations

from collections.abc import Awaitable, Callable, Iterator, Mapping
from contextlib import AbstractContextManager
from dataclasses import dataclass
from typing import Any

from pydantic import BaseModel, ValidationError

from ghrah.protocol.corpus import check_json_serializable
from ghrah.protocol.errors import ApiError
from ghrah.protocol.problem import Problem, ProblemCode

__all__ = [
    "Interface",
    "InterfaceRegistry",
    "InterfaceRuntime",
    "ErrorFactory",
    "Handler",
    "Translator",
]

type Handler = Callable[[Any], Awaitable[Any]]
"""端点处理器：收到已校验的请求信封，返回出参 DTO。"""

type Translator = Callable[[str], AbstractContextManager[None]]
"""``能力名 -> 上下文管理器``：把模块私有异常翻译成统一错误族。"""

type ErrorFactory = Callable[[Problem], ApiError]
"""``Problem -> 异常``：模块可以传自己的子类，默认用 :class:`ApiError`。"""


@dataclass(frozen=True, slots=True)
class Interface:
    """一条能力的声明。

    Attributes:
        name: 能力名，形如 ``"nodes.get"``
        request: 请求信封模型（含被注入的 ``project_id``）
        response: 出参模型；Port 层可能进一步拆壳成裸 id 或 ``None``
    """

    name: str
    request: type[BaseModel]
    response: type[BaseModel]


class InterfaceRegistry:
    """有序、名字唯一、可枚举的能力清单。

    Args:
        capabilities: 能力声明，顺序即枚举顺序

    Raises:
        ValueError: 名字重复
    """

    def __init__(self, capabilities: tuple[Interface, ...]) -> None:
        self._by_name = {capability.name: capability for capability in capabilities}
        if len(self._by_name) != len(capabilities):
            raise ValueError("duplicate capability name")
        self._ordered = capabilities

    def get(self, name: str) -> Interface:
        """按名字取声明。

        Args:
            name: 能力名

        Returns:
            能力声明

        Raises:
            KeyError: 名字未登记
        """
        try:
            return self._by_name[name]
        except KeyError as exc:
            raise KeyError(f"unknown capability: {name}") from exc

    def names(self) -> tuple[str, ...]:
        """全部能力名（登记顺序）。"""
        return tuple(capability.name for capability in self._ordered)

    def __len__(self) -> int:
        return len(self._ordered)

    def __iter__(self) -> Iterator[Interface]:
        return iter(self._ordered)


class InterfaceRuntime:
    """模块内唯一运行时入口。

    Args:
        registry: 本模块的能力清单
        handlers: 能力名 → 处理器
        translator: 本模块的错误翻译表工厂
        error_factory: 用哪种异常包装「查表失败 / 入参非法」；默认 :class:`ApiError`

    Raises:
        RuntimeError: 清单与处理器集合不一致（启动即失败）
    """

    def __init__(
        self,
        *,
        registry: InterfaceRegistry,
        handlers: Mapping[str, Handler],
        translator: Translator,
        error_factory: ErrorFactory = ApiError,
    ) -> None:
        declared = set(registry.names())
        bound = set(handlers)
        if declared != bound:
            raise RuntimeError(
                "capability/handler mismatch: "
                f"missing={sorted(declared - bound)} orphan={sorted(bound - declared)}",
            )
        self._registry = registry
        self._handlers = dict(handlers)
        self._translate = translator
        self._error = error_factory

    async def invoke(self, name: str, payload: Mapping[str, Any]) -> dict[str, Any]:
        """调用一个能力。

        Args:
            name: 能力名，如 ``"nodes.get"``
            payload: 已装箱的入参（由门面用 kwargs 构造）

        Returns:
            JSON 形状的出参；门面负责用出参 DTO 再校验一次

        Raises:
            ApiError: 一切失败（未知能力 / 入参非法 / 内部异常）
        """
        capability = self._require(name)
        request = self._validate_request(capability, payload)
        with self._translate(name):
            result = await self._handlers[name](request)
        return self._validate_response(capability, result)

    # ── 三段强制 ──

    def _require(self, name: str) -> Interface:
        """查表；未知能力是代码缺陷，仍以统一异常族暴露。"""
        try:
            return self._registry.get(name)
        except KeyError as exc:
            raise self._error(
                Problem.make(ProblemCode.INTERNAL, capability="invoke", detail=str(exc)),
            ) from exc

    def _validate_request(self, capability: Interface, payload: Mapping[str, Any]) -> Any:
        """按声明的请求信封校验入参。"""
        try:
            return capability.request.model_validate(dict(payload))
        except ValidationError as exc:
            raise self._error(
                Problem.make(
                    ProblemCode.INVALID_ARGUMENT,
                    capability=capability.name,
                    detail=f"{exc.error_count()} invalid argument(s)",
                    details={"errors": _project_errors(exc)},
                ),
            ) from exc

    def _validate_response(self, capability: Interface, result: Any) -> dict[str, Any]:
        """校出参并强制 JSON 可往返。

        这是「只说 DTO」这条铁律唯一的强制点：只有这里把一切看成 dict。
        """
        response = capability.response.model_validate(result)
        dumped = response.model_dump(mode="json")
        check_json_serializable(dumped, path=capability.name)
        return dumped


def _project_errors(exc: ValidationError) -> list[dict[str, Any]]:
    """把 pydantic 错误投影为 JSON 安全摘要。

    ``exc.errors()`` 的 ``ctx`` 可能带异常对象，直接透出会破坏 JSON 可往返。

    Args:
        exc: pydantic 校验异常

    Returns:
        仅含 ``loc`` / ``type`` / ``msg`` 的列表
    """
    return [
        {
            "loc": [str(part) for part in error.get("loc", ())],
            "type": str(error.get("type", "")),
            "msg": str(error.get("msg", "")),
        }
        for error in exc.errors(include_url=False)
    ]
