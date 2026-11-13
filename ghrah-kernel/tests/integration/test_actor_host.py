# SPDX-FileCopyrightText: 2026 chenxya <chenxya@ghrah.org>
#
# SPDX-License-Identifier: Apache-2.0

"""真实 ActorAgent 的跨进程消息、Context 权威与正常关闭验收。"""

import asyncio
from dataclasses import replace

import pytest
from _runtime_host import A1, U1, identities

from ghrah_kernel.context import ProviderContext
from ghrah_kernel.integration.agent_provider import (
    AGENT_SCHEMES,
    AgentProvider,
    create_resources,
)
from ghrah_kernel.integration.contracts import NAMESPACE_SCHEMES
from ghrah_kernel.integration.identity import ServiceAccessRule
from ghrah_kernel.provider import ProviderFault
from ghrah_kernel.supervisor import ServiceBinding, ServiceSpec, Supervisor


def agent_configuration(database, *, delay=0):
    directory = identities()
    directory = replace(
        directory,
        access=directory.access
        + (
            ServiceAccessRule(
                "chat-u1", "n2", "agent", ("receive", "status", "history"), subject=U1.ref
            ),
        ),
    )
    configuration = {
        "provider": "agent",
        "identities": directory.to_dict(),
        "administrator": U1.ref,
        "principal": A1.ref,
        "namespace": "n2",
        "database": str(database),
        "model": {
            "kind": "scripted",
            "responses": ["第一个真实回复", "第二个真实回复"],
            "delay_seconds": delay,
        },
    }
    return directory, configuration


def setup_supervisor(host):
    return Supervisor(
        host.control,
        (
            ServiceSpec("names", "namespace", entry_key="names"),
            ServiceSpec(
                "actor",
                "agent",
                bindings=(
                    ServiceBinding("/actor", "agent", "s1", "n2"),
                    ServiceBinding("/actor", "agent", "s2", "n2"),
                ),
                close_action=("agent", "close_resources"),
                entry_key="actor",
            ),
        ),
        {"namespace": NAMESPACE_SCHEMES, "agent": AGENT_SCHEMES},
    )


def context(*, root=False):
    return ProviderContext(
        U1.ref,
        "caller",
        "caller-domain",
        "s1",
        "agent-scheme",
        "root" if root else "agent",
        "invocation",
    )


def test_closing_actor_cancels_runner_closes_database_and_is_idempotent(tmp_path):
    async def scenario():
        _, configuration = agent_configuration(tmp_path / "cancel.sqlite3", delay=10)
        resources = await create_resources(configuration, "run-cancel")
        provider = AgentProvider(configuration, resources, "run-cancel")
        task = asyncio.create_task(
            provider.invoke(context(), "receive", {"type": "chat", "text": "cancel-me"})
        )
        async with asyncio.timeout(2):
            while resources.model.calls == 0:
                await asyncio.sleep(0.005)
        receipt = await provider.invoke(context(root=True), "close_resources", {})
        assert receipt.value["closed"]
        assert provider.closed and not resources.backend.connected
        assert not resources.context.in_iteration
        with pytest.raises(asyncio.CancelledError):
            await task
        await provider.aclose()
        with pytest.raises(ProviderFault) as error:
            await provider.invoke(context(), "receive", {"type": "chat", "text": "late"})
        assert error.value.code == "UNAVAILABLE"

    asyncio.run(scenario())


def test_persistence_failure_is_not_reported_as_a_successful_reply(tmp_path, monkeypatch):
    async def scenario():
        _, configuration = agent_configuration(tmp_path / "failure.sqlite3")
        resources = await create_resources(configuration, "run-failure")
        provider = AgentProvider(configuration, resources, "run-failure")

        async def fail():
            raise OSError("disk failure")

        monkeypatch.setattr(resources.context, "persist", fail)
        try:
            with pytest.raises(OSError, match="disk failure"):
                await provider.invoke(
                    context(), "receive", {"type": "chat", "text": "persist-fails"}
                )
            with pytest.raises(OSError, match="disk failure"):
                await provider.aclose()
            assert not resources.backend.connected
        finally:
            monkeypatch.undo()
            with pytest.raises(OSError, match="disk failure"):
                await provider.aclose()

    asyncio.run(scenario())
