# SPDX-FileCopyrightText: 2026 chenxya <chenxya@ghrah.org>
#
# SPDX-License-Identifier: Apache-2.0
"""共享机制测试：能力清单、收敛点四步、启动校验、JSON 强制、翻译注入。"""

from __future__ import annotations

from collections.abc import Iterator
from contextlib import contextmanager

import pytest
from pydantic import BaseModel, ConfigDict

from ghrah.protocol.capability import (
    Capability,
    CapabilityRegistry,
    CapabilityRuntime,
    Handler,
)
from ghrah.protocol.corpus import JsonShapeError
from ghrah.protocol.errors import ApiError
from ghrah.protocol.problem import Problem, ProblemCode


class _Ping(BaseModel):
    model_config = ConfigDict(extra="forbid")

    project_id: str
    name: str = ""


class _Pong(BaseModel):
    greeting: str


class _Ratio(BaseModel):
    ratio: float


class _TestError(ApiError):
    """测试用的模块异常族。"""


def _error(problem: Problem) -> ApiError:
    return _TestError(problem)


@contextmanager
def _translate(capability: str) -> Iterator[None]:
    try:
        yield
    except ApiError:
        raise
    except ValueError as exc:
        raise _TestError(
            Problem.make(ProblemCode.INVALID_ARGUMENT, capability=capability, detail=str(exc)),
        ) from exc
    except Exception as exc:
        raise _TestError(
            Problem.make(
                ProblemCode.INTERNAL,
                capability=capability,
                detail=type(exc).__name__,
            ),
        ) from exc


_REGISTRY = CapabilityRegistry(
    (
        Capability("demo.ping", _Ping, _Pong),
        Capability("demo.ratio", _Ping, _Ratio),
    )
)


async def _ping(request: _Ping) -> _Pong:
    return _Pong(greeting=f"hello {request.name}".strip())


async def _ratio(request: _Ping) -> _Ratio:
    return _Ratio(ratio=1.5)


def _runtime(handlers: dict[str, Handler] | None = None) -> CapabilityRuntime:
    return CapabilityRuntime(
        registry=_REGISTRY,
        handlers=handlers or {"demo.ping": _ping, "demo.ratio": _ratio},
        translator=_translate,
        error_factory=_error,
    )


# ─── 清单 ───


def test_registry_rejects_duplicate_names() -> None:
    with pytest.raises(ValueError, match="duplicate capability name"):
        CapabilityRegistry((Capability("a.b", _Ping, _Pong), Capability("a.b", _Ping, _Pong)))


def test_registry_get_and_names() -> None:
    assert _REGISTRY.names() == ("demo.ping", "demo.ratio")
    assert len(_REGISTRY) == 2
    assert _REGISTRY.get("demo.ping").response is _Pong
    with pytest.raises(KeyError, match="unknown capability"):
        _REGISTRY.get("nope.nope")


# ─── 收敛点 ───


async def test_invoke_returns_json_dict() -> None:
    raw = await _runtime().invoke("demo.ping", {"project_id": "p1", "name": "x"})
    assert raw == {"greeting": "hello x"}


async def test_invoke_rejects_extra_request_fields() -> None:
    with pytest.raises(ApiError) as excinfo:
        await _runtime().invoke("demo.ping", {"project_id": "p1", "extra": 1})
    assert excinfo.value.problem.code is ProblemCode.INVALID_ARGUMENT
    assert excinfo.value.problem.capability == "demo.ping"


async def test_invoke_rejects_missing_required_field() -> None:
    with pytest.raises(ApiError) as excinfo:
        await _runtime().invoke("demo.ping", {})
    assert excinfo.value.problem.code is ProblemCode.INVALID_ARGUMENT


async def test_invoke_unknown_capability_is_internal() -> None:
    with pytest.raises(ApiError) as excinfo:
        await _runtime().invoke("demo.absent", {})
    assert excinfo.value.problem.code is ProblemCode.INTERNAL
    assert excinfo.value.problem.capability == "invoke"


async def test_invoke_translates_handler_exception() -> None:
    async def boom(request: _Ping) -> _Pong:
        raise ValueError("bad input")

    runtime = _runtime({"demo.ping": boom, "demo.ratio": _ratio})
    with pytest.raises(ApiError) as excinfo:
        await runtime.invoke("demo.ping", {"project_id": "p1"})
    assert excinfo.value.problem.code is ProblemCode.INVALID_ARGUMENT
    assert "bad input" in excinfo.value.problem.detail


async def test_invoke_wraps_unexpected_exception() -> None:
    async def boom(request: _Ping) -> _Pong:
        raise RuntimeError("kaput")

    runtime = _runtime({"demo.ping": boom, "demo.ratio": _ratio})
    with pytest.raises(ApiError) as excinfo:
        await runtime.invoke("demo.ping", {"project_id": "p1"})
    assert excinfo.value.problem.code is ProblemCode.INTERNAL
    assert excinfo.value.problem.detail == "RuntimeError"


async def test_invoke_enforces_json_shape() -> None:
    async def nan(request: _Ping) -> _Ratio:
        return _Ratio(ratio=float("nan"))

    runtime = _runtime({"demo.ping": _ping, "demo.ratio": nan})
    with pytest.raises(JsonShapeError, match="non-finite"):
        await runtime.invoke("demo.ratio", {"project_id": "p1"})


async def test_default_error_factory_is_api_error() -> None:
    runtime = CapabilityRuntime(
        registry=_REGISTRY,
        handlers={"demo.ping": _ping, "demo.ratio": _ratio},
        translator=_translate,
    )
    with pytest.raises(ApiError) as excinfo:
        await runtime.invoke("demo.ping", {})
    assert type(excinfo.value) is ApiError


# ─── 启动校验 ───


def test_startup_fails_when_handler_missing() -> None:
    with pytest.raises(RuntimeError, match="capability/handler mismatch"):
        _runtime({"demo.ping": _ping})


def test_startup_fails_when_handler_orphan() -> None:
    async def extra(request: _Ping) -> _Pong:
        return _Pong(greeting="x")

    with pytest.raises(RuntimeError, match="orphan"):
        _runtime({"demo.ping": _ping, "demo.ratio": _ratio, "demo.extra": extra})
