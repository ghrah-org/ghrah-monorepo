# SPDX-FileCopyrightText: 2026 chenxya <chenxya@ghrah.org>
#
# SPDX-License-Identifier: Apache-2.0

"""真实 ActorAgent 的跨进程消息、Context 权威与正常关闭验收。"""

import asyncio
import json
import os
import secrets
from dataclasses import replace

import pytest
from _runtime_host import A1, U1, identities, open_runtime

from ghrah_kernel.client import RemoteError
from ghrah_kernel.context import ProviderContext
from ghrah_kernel.integration.agent_provider import (
    AGENT_SCHEMES,
    AgentProvider,
    create_resources,
)
from ghrah_kernel.integration.contracts import NAMESPACE_SCHEMES
from ghrah_kernel.integration.identity import ServiceAccessRule
from ghrah_kernel.launch import LaunchRecord
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


def test_actor_cross_process_chat_persistence_restart_and_supervisor_shutdown(tmp_path):
    async def scenario():
        database = tmp_path / "context.sqlite3"
        directory, configuration = agent_configuration(database)
        record = LaunchRecord(
            "actor",
            "agent",
            "ghrah_kernel.integration.entry",
            directory.bind(A1.ref, "s1"),
            configuration=json.dumps(configuration),
        )
        async with open_runtime(
            hello=False, directory=directory, extra_launches=(record,), assemble=False
        ) as host:
            supervisor = setup_supervisor(host)
            await supervisor.start(monitor=False)
            assert supervisor.view("actor")["phase"] == "Running", supervisor.view("actor")["error"]
            await host.observer.hello(host.tokens[1])
            await host.peer.hello(host.tokens[2])
            handle = await host.observer.open("/actor")
            with pytest.raises(RemoteError, match="没有"):
                await host.peer.open("/actor")
            before = await host.observer.call(handle, "status", {})
            assert before["principal"] == A1.ref
            assert before["pid"] not in (os.getpid(), host.process.pid)
            assert before["parent_pid"] == host.process.pid
            assert before["model_calls"] == 0
            for arguments in (
                {"type": "command", "text": "no"},
                {"type": "chat", "text": "no", "sender": U1.ref},
                {"type": "chat", "text": "no", "metadata": {"execute_ability": "shell"}},
                {"type": "chat", "text": "no", "recipient": "other"},
                {"type": "chat", "text": "no", "run_id": "forged"},
                {"type": "chat", "text": "x" * 8193},
            ):
                with pytest.raises(RemoteError) as error:
                    await host.observer.call(handle, "receive", arguments)
                assert error.value.code == "INVALID_ARGUMENT"
            with pytest.raises(RemoteError) as error:
                await host.observer.call(handle, "close_resources", {})
            assert error.value.code == "ACCESS_DENIED"
            nonce = secrets.token_hex(12)
            response = await host.observer.call(handle, "receive", {"type": "chat", "text": nonce})
            assert response["content"] == "第一个真实回复"
            assert response["reply_to"] == response["input_id"]
            assert response["sender"] == A1.ref and response["recipient"] == U1.ref
            status = await host.observer.call(handle, "status", {})
            assert status["model_calls"] == 1 and not status["busy"]
            history = await host.observer.call(handle, "history", {"limit": 16})
            assert nonce in json.dumps(history)
            assert "第一个真实回复" in json.dumps(history, ensure_ascii=False)
            assert sum("conversation" in node["ability_names"] for node in history["nodes"]) == 1
            await supervisor.restart_service("actor")
            assert supervisor.view("actor")["phase"] == "Running", supervisor.view("actor")
            with pytest.raises(RemoteError) as stale:
                await host.observer.call(handle, "status", {})
            assert stale.value.code == "STALE_REFERENCE"
            fresh = await host.observer.open("/actor")
            restored = await host.observer.call(fresh, "status", {})
            assert restored["principal"] == before["principal"]
            assert restored["run_id"] != before["run_id"]
            assert restored["context"] == status["context"]
            assert await host.observer.call(fresh, "history", {"limit": 16}) == history
            await host.observer.close(handle)
            await host.observer.close(fresh)
            report = await supervisor.shutdown()
            assert report["clean"], report
            assert len([x for x in supervisor.history if x["event"] == "resources_closed"]) == 2
            assert all(x["reaped"] for x in report["domains"])
            await asyncio.wait_for(host.process.wait(), 5)

        from ghrah.context.persistence.sqlite_backend import SqliteBackend

        reader = SqliteBackend(database, read_only=True)
        await reader.connect()
        try:
            checkpoint = await reader.load_checkpoint(A1.ref)
            assert checkpoint.active_session_id == status["context"]["active_session_id"]
            assert any(nonce in str(node.messages_delta) for node in checkpoint.nodes)
            assert any("第一个真实回复" in str(node.messages_delta) for node in checkpoint.nodes)
        finally:
            await reader.close()

    asyncio.run(scenario())


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
