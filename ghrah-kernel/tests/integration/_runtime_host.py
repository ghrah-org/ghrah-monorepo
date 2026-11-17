# SPDX-FileCopyrightText: 2026 chenxya <chenxya@ghrah.org>
#
# SPDX-License-Identifier: Apache-2.0

"""独立 Kernel、真实命名服务与显式服务夹具的跨进程装配。"""

import asyncio
import json
import os
import secrets
import sys
import tempfile
from contextlib import asynccontextmanager
from dataclasses import asdict, replace
from pathlib import Path

from _integration_probe import SCHEMES

from ghrah_kernel.client import RpcClient
from ghrah_kernel.endpoint import AsyncEndpoint
from ghrah_kernel.integration.contracts import NAMESPACE_SCHEMES
from ghrah_kernel.integration.grants import GrantLifetime
from ghrah_kernel.integration.identity import (
    Group,
    ResolutionScope,
    ResourceNamespace,
    ServiceAccessRule,
    StaticIdentityConfig,
    Subject,
)
from ghrah_kernel.kernel_client import KernelClient
from ghrah_kernel.launch import LaunchRecord
from ghrah_kernel.subprocesses import start_process
from ghrah_kernel.wire import MessageConnection, message_pipe

U1 = Subject("local", "u1", "user")
U2 = Subject("local", "u2", "user")
A1 = Subject("local", "a1", "agent")
SVC = Subject("local", "ns", "service")


def identities():
    return StaticIdentityConfig(
        (U1, U2, A1, SVC),
        (Group("g1", (U1.ref,)),),
        (ResourceNamespace("n1", U1.ref), ResourceNamespace("n2", U2.ref)),
        (
            ResolutionScope("s1", ("n1", "n2"), (U1.ref, A1.ref)),
            ResolutionScope("s2", ("n2",), (U1.ref, U2.ref)),
        ),
        GrantLifetime("instance"),
        (
            ServiceAccessRule(
                "team-read", "n1", "resource", tuple(SCHEMES[0]["actions"]), group="g1"
            ),
            ServiceAccessRule(
                "u2-read", "n2", "resource", tuple(SCHEMES[0]["actions"]), subject=U2.ref
            ),
            ServiceAccessRule(
                "u1-cross", "n2", "resource", tuple(SCHEMES[0]["actions"]), subject=U1.ref
            ),
            ServiceAccessRule("agent-own", "n1", "resource", ("context",), subject=A1.ref),
            ServiceAccessRule(
                "u1-agent", "n2", "agent-entry", ("context", "outbound"), subject=U1.ref
            ),
        ),
    )


class RuntimeHost:
    def __init__(self, clients, process, tokens):
        self.control, self.observer, self.peer = clients
        self.process = process
        self.tokens = tokens
        self.registrations = {}
        self.final_report = None

    async def start(self, key, kind="service-probe"):
        domain = await self.control.spawn(key, kind, spawn_key=secrets.token_hex(8), entry_key=key)
        registration = await self.control.register(
            domain["domain_id"], NAMESPACE_SCHEMES if kind == "namespace" else SCHEMES, "initial"
        )
        self.registrations[key] = registration
        return registration

    async def mount(self, scope, path, key, namespace):
        reference = self.registrations[key]["scheme_refs"]["probe"]
        return await self.control.call(
            self.registrations["names"]["scheme_handles"]["ns"],
            "bind",
            {
                "scope": scope,
                "path": path,
                "target": {**reference, "namespace_id": namespace},
            },
        )


@asynccontextmanager
async def open_runtime(
    *, hello=True, lifetime=None, directory=None, extra_launches=(), assemble=True
):
    directory = directory or identities()
    if lifetime is not None:
        directory = replace(directory, grant_lifetime=lifetime)
    config = {"provider": "namespace", "identities": directory.to_dict(), "administrator": U1.ref}
    records = [
        LaunchRecord(
            "names",
            "namespace",
            "ghrah_kernel.integration.entry",
            directory.bind(SVC.ref, None),
            configuration=json.dumps(config),
        )
    ]
    for key, namespace, resource, principal in (
        ("one", "n1", "resource", SVC.ref),
        ("two", "n2", "resource", SVC.ref),
        ("restricted", "n2", "restricted", SVC.ref),
        ("agent", "n2", "agent-entry", A1.ref),
    ):
        records.append(
            LaunchRecord(
                key,
                "service-probe",
                "_integration_probe",
                directory.bind(principal, "s1" if principal == A1.ref else None),
                environment=(("GHRAH_RUNTIME_PROBE", f"trusted-{key}"),),
                configuration=json.dumps(
                    {
                        "identities": directory.to_dict(),
                        "namespace": namespace,
                        "resource": resource,
                        "label": key,
                    }
                ),
            )
        )
    bindings = {
        "control": directory.bind(U1.ref, "s2", diagnostics=True),
        "observer": directory.bind(U1.ref, "s1"),
        "peer": directory.bind(U2.ref, "s2"),
    }
    records.extend(extra_launches)
    pairs = [message_pipe() for _ in range(3)]
    tokens = [secrets.token_hex(16) for _ in pairs]
    clients = [KernelClient(RpcClient(AsyncEndpoint(MessageConnection(pair[0])))) for pair in pairs]
    process = None
    host = None
    with tempfile.TemporaryDirectory(prefix="ghrah-runtime-") as temporary:
        config_path = Path(temporary) / "host.json"
        report_path = Path(temporary) / "kernel.json"
        config_path.write_text(
            json.dumps(
                {
                    "identities": directory.to_dict(),
                    "launches": [asdict(x) for x in records],
                    "callers": {k: asdict(v) for k, v in bindings.items()},
                }
            )
        )
        try:
            process = await start_process(
                sys.executable,
                "-m",
                "ghrah_kernel",
                "_kernel",
                "--control-fd",
                str(pairs[0][1].fileno()),
                "--control-token",
                tokens[0],
                "--observer-fd",
                str(pairs[1][1].fileno()),
                "--observer-token",
                tokens[1],
                "--peer-fd",
                str(pairs[2][1].fileno()),
                "--peer-token",
                tokens[2],
                "--config",
                str(config_path),
                "--report",
                str(report_path),
                connections=tuple(pair[1] for pair in pairs),
                env={**os.environ, "PYTHONPATH": str(Path(__file__).parent)},
            )
            for _, child in pairs:
                child.close()
            host = RuntimeHost(clients, process, tokens)
            await host.control.hello(tokens[0], requires_ns_root=False)
            if assemble:
                await host.start("names", "namespace")
                for key in ("one", "two", "restricted", "agent"):
                    await host.start(key)
                await host.mount("s1", "/service", "one", "n1")
                await host.mount("s2", "/service", "two", "n2")
                await host.mount("s1", "/agent", "agent", "n2")
                await host.mount("s2", "/agent", "agent", "n2")
                await host.mount("s1", "/restricted", "restricted", "n2")
            if hello:
                await host.observer.hello(tokens[1])
                await host.peer.hello(tokens[2])
            yield host
        finally:
            for _, child in pairs:
                child.close()
            try:
                if process is not None and host is not None and process.returncode is None:
                    await host.control.shutdown()
            finally:
                await asyncio.gather(*(client.aclose() for client in clients))
                if process is not None:
                    try:
                        await asyncio.wait_for(process.wait(), timeout=5)
                    except TimeoutError:
                        process.kill()
                        await process.wait()
                        raise AssertionError("Kernel 未回收受管进程") from None
                    assert process.returncode == 0
                if host is not None and report_path.exists():
                    host.final_report = json.loads(report_path.read_text())
                    assert not host.final_report["errors"]
                    assert all(x["reaped"] for x in host.final_report["domains"])
                    assert host.final_report["stats"]["domains_live"] == 0
