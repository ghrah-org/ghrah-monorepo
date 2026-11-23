# SPDX-FileCopyrightText: 2026 chenxya <chenxya@ghrah.org>
#
# SPDX-License-Identifier: Apache-2.0

"""实体消息投影只读、分页与身份边界。"""

from pathlib import Path

import pytest
from ghrah.protocol.payloads.entity import AgentIdentity

from ghrah.communication.mailbox_reader import MailboxReader
from ghrah.communication.message_manager import MessageKind, MessageManager
from ghrah.core.wiring import build_core_port


async def test_mailbox_scope_pagination_and_offline_reads(tmp_path: Path) -> None:
    db = tmp_path / "messages.sqlite"
    manager = MessageManager(db, project_id="p", agent_ids=["a", "b", "c", "d"])
    await manager.open()
    records = []
    try:
        for n in range(3):
            records.append(
                await manager.send(
                    sender_id="a",
                    recipient_ids=["b", "c"],
                    content=f"item {n}",
                    kind=MessageKind.REQUEST,
                    idempotency_key=str(n),
                )
            )
    finally:
        await manager.close()
    # 管理器关闭后只读投影仍可重建；接收者不能看到其他接收者投递详情。
    async with MailboxReader(db, AgentIdentity(project_id="p", agent_id="b")) as reader:
        first = await reader.list_messages(inbox=True, limit=2, before_seq=None)
        second = await reader.list_messages(
            inbox=True, limit=2, before_seq=first["next_before_seq"]
        )
        items = first["items"] + second["items"]
        assert [item["message"]["message_id"] for item in items] == [
            r.message_id for r in reversed(records)
        ]
        assert all("project_id" not in item["message"] for item in items)
        assert all(len(item["deliveries"]) == 1 for item in items)
        assert all(item["deliveries"][0]["recipient_id"] == "b" for item in items)
        assert all(item["deliveries"][0]["display_state"] == "queued" for item in items)
        detail = await reader.get_delivery(items[0]["deliveries"][0]["delivery_id"])
        assert (detail["state"], detail["display_state"]) == ("pending", "queued")
        assert (await reader.summary())["inbox_counts"] == {"pending": 3}
        assert second["next_before_seq"] is None
    async with MailboxReader(db, AgentIdentity(project_id="p", agent_id="a")) as reader:
        outbox = await reader.list_messages(inbox=False, limit=10, before_seq=None)
        assert len(outbox["items"]) == 3
        assert all(len(item["deliveries"]) == 2 for item in outbox["items"])
    async with MailboxReader(db, AgentIdentity(project_id="p", agent_id="d")) as reader:
        with pytest.raises(ValueError, match="message_not_found"):
            await reader.get_message(records[0].message_id)
    with pytest.raises(ValueError, match="mailbox_project_mismatch"):
        async with MailboxReader(db, AgentIdentity(project_id="other", agent_id="a")):
            pass

    port = build_core_port(scope_id="p", db_path=str(db))
    try:
        message = await port.mailbox.get(agent_id="b", message_id=records[-1].message_id)
        page = await port.mailbox.list(agent_id="b", limit=2)
        assert message.model_dump(mode="json")["project_id"] == ""
        assert [item["project_id"] for item in page.model_dump(mode="json")["items"]] == ["", ""]
    finally:
        await port.aclose()
