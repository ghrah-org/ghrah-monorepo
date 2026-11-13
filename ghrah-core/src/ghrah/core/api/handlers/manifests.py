# SPDX-FileCopyrightText: 2026 chenxya <chenxya@ghrah.org>
#
# SPDX-License-Identifier: Apache-2.0
"""``manifests`` 分组的处理器。

只解析、不落盘。解析源由装配层注入；未注入时明确拒绝，
不伪造一个「解析成功」的结果。
"""

from __future__ import annotations

from typing import Any

from ghrah.protocol.capability import Handler
from ghrah.protocol.problem import Problem, ProblemCode
from ghrah.protocol.views.core import AgentSpecView

from ghrah.core.api.capabilities import ManifestsResolveRequest
from ghrah.core.api.errors import CoreApiError
from ghrah.core.units.registry import RuntimeRegistry

__all__ = ["make_manifest_handlers"]


def _spec_view(manifest_ref: str, raw: Any) -> AgentSpecView:
    """把解析结果投影成契约视图（兼容对象与字典两种形状）。"""
    if isinstance(raw, dict):
        data = dict(raw)
    else:
        dump = getattr(raw, "model_dump", None)
        data = dict(dump(mode="json")) if callable(dump) else dict(vars(raw))
    abilities = data.get("abilities") or data.get("ability_types") or []
    return AgentSpecView(
        manifest_ref=manifest_ref,
        name=str(data.get("name") or ""),
        description=str(data.get("description") or ""),
        system_prompt=str(data.get("system_prompt") or ""),
        abilities=[str(item) for item in abilities],
    )


def make_manifest_handlers(registry: RuntimeRegistry) -> dict[str, Handler]:
    """构造 ``manifests`` 分组处理器。

    Args:
        registry: 运行时注册表（持有可选的 manifest 解析源）

    Returns:
        能力名到处理器的映射
    """

    async def resolve(request: ManifestsResolveRequest) -> AgentSpecView:
        """解析 manifest 引用。"""
        try:
            raw = await registry.resolve_manifest(request.manifest_ref)
        except LookupError as exc:
            raise CoreApiError(
                Problem.make(
                    ProblemCode.FAILED_PRECONDITION,
                    capability="manifests.resolve",
                    detail=str(exc),
                ),
            ) from exc
        return _spec_view(request.manifest_ref, raw)

    return {"manifests.resolve": resolve}
