# SPDX-FileCopyrightText: 2026 chenxya <chenxya@ghrah.org>
#
# SPDX-License-Identifier: Apache-2.0
"""``mailbox`` 分组的处理器。"""

from __future__ import annotations

from typing import Any

from ghrah.protocol.interface import Handler
from ghrah.protocol.problem import Problem, ProblemCode
from ghrah.protocol.views.common import AckView, Page
from ghrah.protocol.views.core import MailboxSummaryView, MessageView, PublishedMessageView

from ghrah.core.api.errors import CoreApiError
from ghrah.core.api.interfaces import (
    MailboxGetRequest,
    MailboxListRequest,
    MailboxPublishExternalRequest,
    MailboxPublishNotificationRequest,
    MailboxRecoverProjectionsRequest,
    MailboxSummaryRequest,
)
from ghrah.core.read.mailbox import MailboxUnavailableError
from ghrah.core.read.queries import MailboxQueries

__all__ = ["make_mailbox_handlers"]

_EMPTY_SUMMARY = MailboxSummaryView(availability="empty", inbox_counts={}, outbox_count=0)


def _manager(registry: Any, capability: str) -> Any:
    """取作用域消息管理器；单元未启动时抛 ``UNAVAILABLE``。"""
    manager = registry.message_manager()
    if manager is None:
        raise CoreApiError(
            Problem.make(
                ProblemCode.UNAVAILABLE,
                capability=capability,
                detail="message manager unavailable for this scope",
            ),
        )
    return manager


def make_mailbox_handlers(store: MailboxQueries, registry: Any) -> dict[str, Handler]:
    """构造 ``mailbox`` 分组处理器。

    Args:
        store: 消息表只读适配层
        registry: 运行时注册表（发布能力经它取消息管理器）

    Returns:
        能力名到处理器的映射
    """

    async def summary(request: MailboxSummaryRequest) -> MailboxSummaryView:
        """读取信箱概览。消息库不存在时返回 ``empty``，不报错。"""
        try:
            return await store.summary(agent_id=request.agent_id)
        except MailboxUnavailableError:
            return _EMPTY_SUMMARY

    async def list_messages(request: MailboxListRequest) -> Page[MessageView]:
        """分页列出收件箱/发件箱。消息库不存在时返回空页。"""
        before_seq = int(request.cursor) if request.cursor else None
        try:
            items, next_seq = await store.list_messages(
                agent_id=request.agent_id,
                inbox=request.inbox,
                limit=request.limit,
                before_seq=before_seq,
            )
        except MailboxUnavailableError:
            return Page[MessageView]()
        return Page[MessageView](
            items=items,
            next_cursor=str(next_seq) if next_seq is not None else None,
            has_more=next_seq is not None,
        )

    async def get_message(request: MailboxGetRequest) -> MessageView:
        """读取单条可见消息。"""
        return await store.get_message(
            agent_id=request.agent_id,
            message_id=request.message_id,
        )

    async def publish_external(request: MailboxPublishExternalRequest) -> PublishedMessageView:
        """以外部（人）身份发布消息：写入消息表并投递给收件人。"""
        await registry.ensure()
        manager = _manager(registry, "mailbox.publish_external")
        metadata = request.metadata
        if request.content_blocks is not None:
            metadata = {
                **(metadata or {}),
                "content_blocks": [
                    block.model_dump(mode="json", exclude_none=True)
                    for block in request.content_blocks
                ],
            }
        record = await manager.send_external(
            sender=request.sender,
            recipient_ids=tuple(request.recipient_ids),
            content=request.content,
            idempotency_key=request.idempotency_key,
            metadata=metadata,
        )
        return PublishedMessageView(message_id=str(record.message_id))

    async def publish_notification(
        request: MailboxPublishNotificationRequest,
    ) -> PublishedMessageView:
        """发布通知类消息，并按指定收件人投递。"""
        from ghrah.communication import MessageKind

        await registry.ensure()
        manager = _manager(registry, "mailbox.publish_notification")
        record = await manager.send(
            sender_id=request.sender_id,
            recipient_ids=tuple(request.recipient_ids),
            content=request.content,
            kind=MessageKind(request.kind),
            idempotency_key=request.idempotency_key,
            metadata=request.metadata,
        )
        return PublishedMessageView(message_id=str(record.message_id))

    async def recover_room_projections(request: MailboxRecoverProjectionsRequest) -> AckView:
        """补齐缺失的 Room 投影日志。"""
        await registry.ensure()
        manager = _manager(registry, "mailbox.recover_room_projections")
        await manager.recover_room_projections()
        return AckView(ok=True)

    return {
        "mailbox.summary": summary,
        "mailbox.list": list_messages,
        "mailbox.get": get_message,
        "mailbox.publish_external": publish_external,
        "mailbox.publish_notification": publish_notification,
        "mailbox.recover_room_projections": recover_room_projections,
    }
