# SPDX-FileCopyrightText: 2026 chenxya <chenxya@ghrah.org>
# SPDX-License-Identifier: Apache-2.0
"""从绑定和契约重建只读目录，不保存第二份目录或授权状态。"""

from collections.abc import Awaitable, Callable

from ghrah.protocol.scheme import DiscoveredAction, DiscoveredResource

from ghrah.scheme._state import Registration
from ghrah.scheme.models import (
    AccessRequest,
    AccessStage,
    Authorization,
    ProviderState,
    Requester,
    Target,
)


async def project_resource(
    principal: Requester,
    target: Target,
    registration: Registration,
    authorize: Callable[[AccessRequest], Awaitable[Authorization]],
    valid: Callable[[], bool],
) -> DiscoveredResource | None:
    actions = []
    # 完整描述只从已冻结的唯一作者模型导出，schema 不再现场独立重建。
    for action in registration.spec.descriptor().actions:
        request = AccessRequest(principal, target, frozenset({action.name}), AccessStage.DISCOVER)
        if not (await authorize(request)).allowed:
            continue
        readable = (
            await authorize(AccessRequest(principal, target, request.actions, AccessStage.DESCRIBE))
        ).allowed
        invokable = (
            await authorize(AccessRequest(principal, target, request.actions, AccessStage.INVOKE))
        ).allowed
        # 异步核定之后检查绑定、实例及会话，失效时丢弃整项。
        if not valid():
            return None
        actions.append(
            DiscoveredAction(
                name=action.name,
                description=action.description,
                mutating=action.mutating,
                cancellable=action.cancellable,
                invokable=invokable and registration.state is ProviderState.READY,
                input_schema=action.input_schema if readable else None,
                output_schema=action.output_schema if readable else None,
            )
        )
    if not actions or not valid():
        return None

    return DiscoveredResource(
        kind=target.kind,
        alias=target.alias,
        contract=target.contract,
        contract_version=target.contract_version,
        contract_digest=target.contract_digest,
        binding_revision=target.binding_revision,
        available=registration.state is ProviderState.READY,
        actions=actions,
    )
