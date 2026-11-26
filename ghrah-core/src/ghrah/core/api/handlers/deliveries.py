# SPDX-FileCopyrightText: 2026 chenxya <chenxya@ghrah.org>
#
# SPDX-License-Identifier: Apache-2.0
"""``deliveries`` 分组的处理器。"""

from __future__ import annotations

from ghrah.protocol.interface import Handler
from ghrah.protocol.problem import Problem, ProblemCode
from ghrah.protocol.views.common import AckView
from ghrah.protocol.views.core import DeliveryQueueView, DeliveryView

from ghrah.core.api.errors import CoreApiError
from ghrah.core.api.interfaces import (
    DeliveriesCancelRequest,
    DeliveriesGetRequest,
    DeliveriesQueueRequest,
    DeliveriesResumeRequest,
    DeliveriesRetryRequest,
    RoomsRecoverRequest,
)
from ghrah.core.read.queries import MailboxQueries
from ghrah.core.units.registry import RuntimeRegistry

__all__ = ["make_delivery_handlers"]


def make_delivery_handlers(
    store: MailboxQueries,
    registry: RuntimeRegistry,
) -> dict[str, Handler]:
    """构造 ``deliveries`` 分组处理器。

    Args:
        store: 消息表只读适配层（投递与消息同库）
        registry: 运行时注册表（提供消息管理器）

    Returns:
        能力名到处理器的映射
    """

    async def get_delivery(request: DeliveriesGetRequest) -> DeliveryView:
        """读取单条可见投递。"""
        return await store.get_delivery(
            agent_id=request.agent_id,
            delivery_id=request.delivery_id,
        )

    async def queue(request: DeliveriesQueueRequest) -> DeliveryQueueView:
        """查询本实体指定会话的实时队列。"""
        return await store.queue(agent_id=request.agent_id, session_id=request.session_id)

    async def cancel(request: DeliveriesCancelRequest) -> AckView:
        """校验投递可见性后原子取消排队项。"""
        await store.get_delivery(agent_id=request.agent_id, delivery_id=request.delivery_id)
        try:
            await registry.cancel_delivery(delivery_id=request.delivery_id)
        except ValueError as exc:
            raise CoreApiError(
                Problem.make(
                    ProblemCode.FAILED_PRECONDITION,
                    capability="deliveries.cancel",
                    detail=str(exc),
                    details={"reason": str(exc)},
                )
            ) from exc
        return AckView(ok=True)

    async def retry(request: DeliveriesRetryRequest) -> AckView:
        """重试一条失败投递（先校验该投递对本实体可见）。"""
        await store.get_delivery(
            agent_id=request.agent_id,
            delivery_id=request.delivery_id,
        )
        await registry.retry_delivery(delivery_id=request.delivery_id)
        return AckView(ok=True)

    async def resume(request: DeliveriesResumeRequest) -> AckView:
        """恢复一条中断投递（同样先校验可见性）。"""
        await store.get_delivery(
            agent_id=request.agent_id,
            delivery_id=request.delivery_id,
        )
        await registry.resume_delivery(delivery_id=request.delivery_id)
        return AckView(ok=True)

    async def recover(request: RoomsRecoverRequest) -> AckView:
        """修复 Room 投影。"""
        await registry.recover_rooms()
        return AckView(ok=True)

    return {
        "deliveries.get": get_delivery,
        "deliveries.queue": queue,
        "deliveries.cancel": cancel,
        "deliveries.retry": retry,
        "deliveries.resume": resume,
        "rooms.recover": recover,
    }
