# SPDX-FileCopyrightText: 2026 chenxya <chenxya@ghrah.org>
#
# SPDX-License-Identifier: Apache-2.0

"""在 wheel 隔离环境验收真实 ActorAgent 与 Context，不依赖源码测试夹具。"""

import asyncio
import importlib.util
import json
import os
import secrets
import tempfile
from dataclasses import asdict
from pathlib import Path

from ghrah.context.persistence.sqlite_backend import SqliteBackend
from ghrah_kernel.client import RpcClient
from ghrah_kernel.endpoint import AsyncEndpoint
from ghrah_kernel.integration.agent_provider import AGENT_SCHEMES
from ghrah_kernel.integration.assembly import host_configuration
from ghrah_kernel.integration.contracts import NAMESPACE_SCHEMES
from ghrah_kernel.integration.grants import GrantLifetime
from ghrah_kernel.integration.identity import (
    ResolutionScope,
    ResourceNamespace,
    ServiceAccessRule,
    StaticIdentityConfig,
    Subject,
)
from ghrah_kernel.kernel.runtime import Kernel
from ghrah_kernel.kernel_client import KernelClient
from ghrah_kernel.launch import LaunchRecord
from ghrah_kernel.supervisor import ServiceBinding, ServiceSpec, Supervisor
from ghrah_kernel.wire import MessageConnection, message_pipe


async def verify(database: Path) -> dict:
    """真实启动、调用、停机后，从只读连接复核唯一 Context 数据库。"""
    assert importlib.util.find_spec("_mechanism") is None
    user, agent, names = (
        Subject("smoke", "user", "user"),
        Subject("smoke", "actor", "agent"),
        Subject("smoke", "names", "service"),
    )
    directory = StaticIdentityConfig(
        (user, agent, names),
        (),
        (ResourceNamespace("workspace", user.ref),),
        (ResolutionScope("local", ("workspace",), (user.ref, agent.ref)),),
        GrantLifetime("instance"),
        (
            ServiceAccessRule(
                "chat", "workspace", "agent", ("receive", "status", "history"), subject=user.ref
            ),
        ),
    )
    records = [
        LaunchRecord(
            key,
            kind,
            "ghrah_kernel.integration.entry",
            directory.bind(subject.ref, scope),
            configuration=json.dumps(configuration),
        )
        for key, kind, subject, scope, configuration in (
            (
                "names",
                "namespace",
                names,
                None,
                {"provider": "namespace", "administrator": user.ref},
            ),
            (
                "actor",
                "agent",
                agent,
                "local",
                {
                    "provider": "agent",
                    "administrator": user.ref,
                    "principal": agent.ref,
                    "namespace": "workspace",
                    "database": str(database),
                    "model": {
                        "kind": "scripted",
                        "responses": ["wheel 中的真实 Actor 回复"],
                        "delay_seconds": 0,
                    },
                },
            ),
        )
    ]
    catalog, bindings = host_configuration(
        {
            "identities": directory.to_dict(),
            "launches": [asdict(x) for x in records],
            "callers": {
                name: asdict(directory.bind(user.ref, "local"))
                for name in ("control", "observer", "peer")
            },
        }
    )
    kernel = Kernel(launch_catalog=catalog)
    clients = []

    def connect(name, role):
        own, peer = message_pipe()
        token = secrets.token_hex(16)
        kernel.attach_caller(peer, token, role, **asdict(bindings[name]))
        client = KernelClient(RpcClient(AsyncEndpoint(MessageConnection(own))))
        clients.append(client)
        return client, token

    try:
        control, token = connect("control", "bootstrap")
        await control.hello(token, requires_ns_root=False)
        supervisor = Supervisor(
            control,
            (
                ServiceSpec("names", "namespace", entry_key="names"),
                ServiceSpec(
                    "actor",
                    "agent",
                    entry_key="actor",
                    bindings=(ServiceBinding("/actor", "agent", "local", "workspace"),),
                    close_action=("agent", "close_resources"),
                ),
            ),
            {"namespace": NAMESPACE_SCHEMES, "agent": AGENT_SCHEMES},
        )
        await supervisor.start(monitor=False)
        assert supervisor.view("actor")["phase"] == "Running", supervisor.view("actor")["error"]
        observer, token = connect("observer", "observer")
        await observer.hello(token)
        handle = await observer.open("/actor")
        status = await observer.call(handle, "status", {})
        assert status["parent_pid"] == os.getpid() and status["pid"] != os.getpid()
        nonce = secrets.token_hex(16)
        reply = await observer.call(handle, "receive", {"type": "chat", "text": nonce})
        assert reply["content"] == "wheel 中的真实 Actor 回复"
        assert reply["sender"] == agent.ref and reply["recipient"] == user.ref
        await observer.close(handle)
        report = await supervisor.shutdown()
        assert report["clean"] and all(x["reaped"] for x in report["domains"])
        assert any(x["event"] == "resources_closed" for x in supervisor.history)
    finally:
        await kernel.aclose()
        await asyncio.gather(*(client.aclose() for client in clients))
    reader = SqliteBackend(database, read_only=True)
    await reader.connect()
    try:
        checkpoint = await reader.load_checkpoint(agent.ref)
        assert any(nonce in str(node.messages_delta) for node in checkpoint.nodes)
        assert any(
            "wheel 中的真实 Actor 回复" in str(node.messages_delta) for node in checkpoint.nodes
        )
    finally:
        await reader.close()
    return {
        "passed": True,
        "actor_pid": status["pid"],
        "model": "scripted",
        "children_reaped": len(report["domains"]),
        "context_read_back": True,
    }


if __name__ == "__main__":
    with tempfile.TemporaryDirectory(prefix="agent-wheel-") as temporary:
        print(json.dumps(asyncio.run(verify(Path(temporary) / "context.sqlite3"))))
