# SPDX-FileCopyrightText: 2026 chenxya <chenxya@ghrah.org>
#
# SPDX-License-Identifier: Apache-2.0
"""Core 的唯一对外异常族与内部异常翻译表。

:class:`CoreApiError` 是契约包 :class:`ghrah.protocol.errors.ApiError` 的子类——
消费方 ``except ApiError`` 即可，不需要 import Core。
"""

from __future__ import annotations

import logging
from collections.abc import Iterator
from contextlib import contextmanager

from ghrah.protocol.errors import ApiError
from ghrah.protocol.problem import Problem, ProblemCode
from pydantic import ValidationError

from ghrah.ability.access import AccessError as AbilityAccessError
from ghrah.core.read.ability import AbilityConflictError, AbilityUnavailableError
from ghrah.core.read.ability_catalog import CatalogError
from ghrah.core.read.chain import ChainDatabaseUnavailableError
from ghrah.core.read.mailbox import (
    DeliveryNotFoundError,
    MailboxProjectMismatchError,
    MailboxUnavailableError,
    MessageNotFoundError,
)

logger = logging.getLogger(__name__)

__all__ = ["CoreApiError", "translate"]


class CoreApiError(ApiError):
    """Core 端口抛出的异常类型（``ApiError`` 的语义化子类）。"""


@contextmanager
def translate(capability: str) -> Iterator[None]:
    """把内部异常翻译成统一错误族；绝不让陌生异常穿出模块。

    Args:
        capability: 能力名，用于定位

    Yields:
        原样放行正常路径

    Raises:
        CoreApiError: 任何内部异常的统一包装
    """
    try:
        yield
    except CoreApiError:
        raise
    except ChainDatabaseUnavailableError as exc:
        raise CoreApiError(
            Problem.make(ProblemCode.NOT_FOUND, capability=capability, detail=str(exc)),
        ) from exc
    except MailboxUnavailableError as exc:
        raise CoreApiError(
            Problem.make(ProblemCode.NOT_FOUND, capability=capability, detail=str(exc)),
        ) from exc
    except MessageNotFoundError as exc:
        raise CoreApiError(
            Problem.make(ProblemCode.NOT_FOUND, capability=capability, detail=str(exc)),
        ) from exc
    except DeliveryNotFoundError as exc:
        raise CoreApiError(
            Problem.make(ProblemCode.NOT_FOUND, capability=capability, detail=str(exc)),
        ) from exc
    except MailboxProjectMismatchError as exc:
        raise CoreApiError(
            Problem.make(
                ProblemCode.FAILED_PRECONDITION,
                capability=capability,
                detail=str(exc),
            ),
        ) from exc
    except AbilityConflictError as exc:
        details: dict[str, object] = {}
        if exc.revision is not None:
            details["revision"] = exc.revision
        if exc.view is not None:
            details["view"] = exc.view.model_dump(mode="json")
        raise CoreApiError(
            Problem.make(
                ProblemCode.FAILED_PRECONDITION,
                capability=capability,
                detail="conflict",
                details=details,
            ),
        ) from exc
    except CatalogError as exc:
        mapping = {
            "not_found": ProblemCode.NOT_FOUND,
            "invalid_request": ProblemCode.INVALID_ARGUMENT,
            "unavailable": ProblemCode.UNAVAILABLE,
        }
        raise CoreApiError(
            Problem.make(
                mapping.get(exc.code, ProblemCode.INTERNAL),
                capability=capability,
                detail=exc.message,
            ),
        ) from exc
    except AbilityUnavailableError as exc:
        raise CoreApiError(
            Problem.make(
                ProblemCode.UNAVAILABLE,
                capability=capability,
                detail=str(exc),
            ),
        ) from exc
    except AbilityAccessError as exc:
        reason = str(exc)
        code = (
            ProblemCode.NOT_FOUND
            if reason in {"unavailable", "not_found"}
            else ProblemCode.FAILED_PRECONDITION
        )
        raise CoreApiError(
            Problem.make(code, capability=capability, detail=reason),
        ) from exc
    except (ValueError, ValidationError) as exc:
        raise CoreApiError(
            Problem.make(
                ProblemCode.INVALID_ARGUMENT,
                capability=capability,
                detail=str(exc),
            ),
        ) from exc
    except Exception as exc:  # 兜底：任何陌生异常都不许穿出边界
        logger.exception("capability %s raised unexpected error", capability)
        raise CoreApiError(
            Problem.make(
                ProblemCode.INTERNAL,
                capability=capability,
                detail=f"{type(exc).__name__}: {exc}",
            ),
        ) from exc
