# SPDX-FileCopyrightText: 2026 chenxya <chenxya@ghrah.org>
#
# SPDX-License-Identifier: Apache-2.0
"""信箱（mm_*）表族读取。

与 ``chain`` 同一纪律：**表读取只在这一层**，上层只拿 DTO。

实现上复用 :class:`ghrah.communication.mailbox_reader.MailboxReader`——
它已经封装了「只读连接 + 项目归属校验 + 收件人可见性过滤」这三件正确性关键事，
不在这里重写 SQL。

状态集合（``mm_deliveries.state`` 的 CHECK 约束）：
``pending`` / ``processing`` / ``completed`` / ``failed`` / ``interrupted``，
另加派生值 ``blocked``（``blocked_reason`` 非空时覆盖 state）。
"""

from __future__ import annotations

from pathlib import Path

from ghrah.protocol.payloads.entity import AgentIdentity
from ghrah.protocol.views.core import (
    DeliveryView,
    MailboxSummaryView,
    MessageView,
)

from ghrah.communication.mailbox_reader import MailboxReader
from ghrah.core.read.chain import DbPathResolver

__all__ = [
    "DeliveryNotFoundError",
    "MailboxBadPageSizeError",
    "MailboxProjectMismatchError",
    "MailboxReadStore",
    "MailboxUnavailableError",
    "MessageNotFoundError",
]


class MailboxUnavailableError(RuntimeError):
    """消息库不存在或缺少 mm_meta 表（项目还没产生消息）。"""


class MailboxProjectMismatchError(RuntimeError):
    """消息库归属的项目与请求不一致。"""


class MessageNotFoundError(LookupError):
    """消息不存在，或本实体不可见。"""


class DeliveryNotFoundError(LookupError):
    """投递不存在，或本实体不可见。"""


class MailboxBadPageSizeError(ValueError):
    """分页大小越界。"""


class MailboxReadStore:
    """按「项目 + 实体」读取信箱。

    Args:
        resolve_db_path: 项目到 ActionChain sqlite 路径的解析器（与 chain 共用同一文件）
    """

    def __init__(self, resolve_db_path: DbPathResolver) -> None:
        self._resolve = resolve_db_path

    async def _reader(self, project_id: str, agent_id: str) -> MailboxReader:
        """构建并打开一次查询用的 reader（上下文管理器由调用方负责）。"""
        path = Path(self._resolve(project_id))
        if not path.is_file():
            raise MailboxUnavailableError(str(path))
        return MailboxReader(path, AgentIdentity(project_id=project_id, agent_id=agent_id))

    async def summary(self, *, project_id: str, agent_id: str) -> MailboxSummaryView:
        """读取信箱概览。

        Args:
            project_id: 项目标识
            agent_id: 稳定 Agent ID

        Returns:
            概览视图

        Raises:
            MailboxUnavailableError: 消息库不存在
            MailboxProjectMismatchError: 项目归属不一致
        """
        reader = await self._reader(project_id, agent_id)
        try:
            async with reader:
                raw = await reader.summary()
        except ValueError as exc:
            raise _map_reader_error(exc) from exc
        return MailboxSummaryView.model_validate(raw)

    async def get_message(self, *, project_id: str, agent_id: str, message_id: str) -> MessageView:
        """读取单条消息（含可见投递记录）。

        Args:
            project_id: 项目标识
            agent_id: 稳定 Agent ID
            message_id: 消息标识

        Returns:
            消息视图

        Raises:
            MessageNotFoundError: 消息不存在或不可见
            MailboxUnavailableError: 消息库不存在
        """
        reader = await self._reader(project_id, agent_id)
        try:
            async with reader:
                raw = await reader.get_message(message_id)
        except ValueError as exc:
            raise _map_reader_error(exc) from exc
        return _message_view(raw)

    async def get_delivery(
        self, *, project_id: str, agent_id: str, delivery_id: str
    ) -> DeliveryView:
        """读取单条可见投递。

        Args:
            project_id: 项目标识
            agent_id: 稳定 Agent ID
            delivery_id: 投递标识

        Returns:
            投递视图

        Raises:
            DeliveryNotFoundError: 投递不存在或不可见
            MailboxUnavailableError: 消息库不存在
        """
        reader = await self._reader(project_id, agent_id)
        try:
            async with reader:
                raw = await reader.get_delivery(delivery_id)
        except ValueError as exc:
            raise _map_reader_error(exc) from exc
        return DeliveryView.model_validate(raw)

    async def list_messages(
        self,
        *,
        project_id: str,
        agent_id: str,
        inbox: bool,
        limit: int,
        before_seq: int | None,
    ) -> tuple[list[MessageView], int | None]:
        """分页列出收件箱/发件箱。

        Args:
            project_id: 项目标识
            agent_id: 稳定 Agent ID
            inbox: ``True`` 列收件箱，``False`` 列发件箱
            limit: 页大小（1..200）
            before_seq: 上一页返回的游标；``None`` 表示第一页

        Returns:
            ``(items, next_before_seq)``；``next_before_seq`` 为 ``None`` 表示没有下一页

        Raises:
            MailboxBadPageSizeError: ``limit`` 越界
            MailboxUnavailableError: 消息库不存在
        """
        reader = await self._reader(project_id, agent_id)
        try:
            async with reader:
                raw = await reader.list_messages(inbox=inbox, limit=limit, before_seq=before_seq)
        except ValueError as exc:
            raise _map_reader_error(exc) from exc
        items = [_message_view(item) for item in raw["items"]]
        next_seq = raw.get("next_before_seq")
        return items, int(next_seq) if next_seq is not None else None


def _message_view(raw: dict[str, object]) -> MessageView:
    """把 reader 的 ``{message, deliveries}`` 投影成契约视图。"""
    message = dict(raw.get("message") or {})
    deliveries = raw.get("deliveries") or []
    message["deliveries"] = [DeliveryView.model_validate(item) for item in deliveries]
    return MessageView.model_validate(message)


def _map_reader_error(exc: ValueError) -> Exception:
    """把 reader 的稳定字符串码映射成本层的类型化异常。"""
    code = str(exc)
    if code == "message_not_found":
        return MessageNotFoundError(code)
    if code == "delivery_not_found":
        return DeliveryNotFoundError(code)
    if code == "mailbox_project_mismatch":
        return MailboxProjectMismatchError(code)
    if code == "invalid_page_size":
        return MailboxBadPageSizeError(code)
    return MailboxUnavailableError(code)
