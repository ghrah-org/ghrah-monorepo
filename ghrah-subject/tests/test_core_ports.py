# SPDX-FileCopyrightText: 2026 chenxya <chenxya@ghrah.org>
#
# SPDX-License-Identifier: Apache-2.0

"""``CorePorts``：项目 → Core 端口实例的注册表。

覆盖：懒建与缓存、定位符登记与委托、关闭后重建、批量关闭。
"""

from __future__ import annotations

from typing import Any

import pytest

from ghrah.subject.runtime.core_ports import CorePorts


class _FakePort:
    """最小端口替身：只记录被关闭次数。"""

    def __init__(self) -> None:
        self.closed = 0

    async def aclose(self) -> None:
        self.closed += 1


def _recording_factory(created: list[str]):
    def factory(project_id: str) -> Any:
        created.append(project_id)
        return _FakePort()

    return factory


def test_for_project_lazily_creates_and_caches() -> None:
    created: list[str] = []
    ports = CorePorts(_recording_factory(created))

    assert ports.has_project("p1") is False
    first = ports.for_project("p1")
    second = ports.for_project("p1")

    assert first is second
    assert created == ["p1"]
    assert ports.has_project("p1") is True


def test_for_project_requires_project_id() -> None:
    ports = CorePorts(_recording_factory([]))
    with pytest.raises(ValueError, match="project_id required"):
        ports.for_project("")


def test_locator_registry_delegates() -> None:
    ports = CorePorts(_recording_factory([]))

    assert ports.locator_for("p1") is None
    ports.remember_locator("p1", "file:///tmp/p1")
    assert ports.locator_for("p1") == "file:///tmp/p1"

    # 空值不登记（避免把空定位符当成有效项目根）
    ports.remember_locator("p2", "")
    assert ports.locator_for("p2") is None


def test_factory_can_read_locator() -> None:
    ports = CorePorts(lambda project_id: _FakePort())
    ports.remember_locator("p1", "file:///tmp/p1")

    def factory(project_id: str) -> Any:
        locator = ports.locator_for(project_id)
        assert locator == "file:///tmp/p1"
        return _FakePort()

    ports = CorePorts(factory)
    ports.remember_locator("p1", "file:///tmp/p1")
    ports.for_project("p1")


async def test_close_project_closes_and_allows_recreate() -> None:
    created: list[str] = []
    ports = CorePorts(_recording_factory(created))

    first = ports.for_project("p1")
    await ports.close_project("p1")

    assert first.closed == 1
    assert ports.has_project("p1") is False

    second = ports.for_project("p1")
    assert second is not first
    assert created == ["p1", "p1"]

    # 关闭未构造的项目无操作
    await ports.close_project("unknown")


async def test_aclose_closes_all_ports() -> None:
    created: list[str] = []
    ports = CorePorts(_recording_factory(created))

    first = ports.for_project("p1")
    second = ports.for_project("p2")
    await ports.aclose()

    assert first.closed == 1
    assert second.closed == 1
    assert ports.has_project("p1") is False
    assert ports.has_project("p2") is False


async def test_entity_mailbox_cancel_forwards_and_preserves_scope(monkeypatch) -> None:
    import asyncio
    from types import SimpleNamespace
    from unittest.mock import AsyncMock

    from ghrah.protocol.payloads.entity import AgentEntityPayload
    from ghrah.protocol.views.core import DeliveryView

    from ghrah.subject.project.entities import AgentEntityManager
    from ghrah.subject.project.models import ProjectStatus
    from ghrah.subject.runtime.port_transport import PortMailbox

    delivery = DeliveryView(delivery_id="delivery", recipient_id="agent", state="pending")
    cancelled = delivery.model_copy(
        update={"state": "interrupted", "display_state": "cancelled", "error": "cancelled_by_user"}
    )
    operations = SimpleNamespace(
        get=AsyncMock(side_effect=[delivery, cancelled]),
        cancel=AsyncMock(),
    )
    mailbox = PortMailbox(SimpleNamespace(deliveries=operations))
    projects = SimpleNamespace(
        lifecycle_lock=lambda _: asyncio.Lock(),
        _cluster_transport=SimpleNamespace(get_message_manager=lambda _: mailbox),
    )
    manager = AgentEntityManager(projects)
    monkeypatch.setattr(
        manager,
        "_load",
        AsyncMock(
            return_value=(
                SimpleNamespace(project_id="p", status=ProjectStatus.ACTIVE),
                SimpleNamespace(agent_id="agent", desired_state="stopped"),
            )
        ),
    )
    monkeypatch.setattr(manager, "_runtime", AsyncMock(return_value={"availability": "online"}))
    result = await manager.execute(
        AgentEntityPayload.model_validate(
            {
                "project_id": "p",
                "agent_id": "agent",
                "operation": {"type": "mailbox.cancel", "delivery_id": "delivery"},
            }
        )
    )
    assert result["delivery"]["display_state"] == "cancelled"
    operations.cancel.assert_awaited_once_with(agent_id="agent", delivery_id="delivery")
    assert operations.get.await_count == 2
    manager._runtime.assert_not_awaited()
