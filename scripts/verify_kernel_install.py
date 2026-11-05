# SPDX-FileCopyrightText: 2026 chenxya <chenxya@ghrah.org>
#
# SPDX-License-Identifier: Apache-2.0

"""在独立 wheel 环境验证命名服务启动、Scope 与收尾，不导入测试夹具。"""

import asyncio
import importlib.util
import json
import math
import secrets
from multiprocessing import Pipe

from ghrah_kernel.client import RpcClient
from ghrah_kernel.endpoint import AsyncEndpoint
from ghrah_kernel.integration.contracts import NAMESPACE_SCHEMES
from ghrah_kernel.integration.grants import GrantLifetime
from ghrah_kernel.integration.identity import (
    ResolutionScope,
    ResourceNamespace,
    StaticIdentityConfig,
    Subject,
)
from ghrah_kernel.kernel.runtime import Kernel
from ghrah_kernel.kernel_client import KernelClient
from ghrah_kernel.launch import LaunchCatalog, LaunchRecord
from ghrah_kernel.wire import MessageConnection


async def verify() -> dict:
    """实际创建并注册命名服务进程，从普通连接打开自身解析根。"""
    assert importlib.util.find_spec("_mechanism") is None
    assert importlib.util.find_spec("ghrah") is None
    user, service = Subject("smoke", "user", "user"), Subject("smoke", "ns", "service")
    directory = StaticIdentityConfig(
        (user, service),
        (),
        (ResourceNamespace("workspace", user.ref),),
        (ResolutionScope("local", ("workspace",), (user.ref,)),),
        GrantLifetime("instance"),
    )
    record = LaunchRecord(
        "names",
        "namespace",
        "ghrah_kernel.integration.entry",
        directory.bind(service.ref, None),
        configuration=json.dumps(
            {"provider": "namespace", "identities": directory.to_dict(), "administrator": user.ref}
        ),
    )
    kernel = Kernel(launch_catalog=LaunchCatalog((record,)))
    clients = []

    def connect(role):
        own, peer = Pipe(duplex=True)
        token = secrets.token_hex(16)
        kernel.attach_caller(peer, token, role, principal=user.ref, resolution_scope="local")
        client = KernelClient(RpcClient(AsyncEndpoint(MessageConnection(own))))
        clients.append(client)
        return client, token

    try:
        admin, token = connect("bootstrap")
        await admin.hello(token, requires_ns_root=False)
        domain = await admin.spawn("names", "namespace", spawn_key="once", entry_key="names")
        await admin.register(domain["domain_id"], NAMESPACE_SCHEMES, "initial")
        user_client, token = connect("observer")
        await user_client.hello(token)
        receipt = await user_client._request(
            "open", base=user_client.session["ns_root_handle"], path="/"
        )
        assert receipt.meta["expiry"] == math.inf
        opened = receipt.meta["handle_id"]
        view = await user_client.call(opened, "inspect", {})
        assert view == {"scope": "local", "principal": user.ref, "paths": []}
        await user_client.close(opened)
        await admin.shutdown()
    finally:
        await kernel.aclose()
        await asyncio.gather(*(client.aclose() for client in clients))
    report = kernel.report()
    assert not report["errors"] and report["stats"]["domains_live"] == 0
    assert all(domain["reaped"] for domain in report["domains"])
    return {"passed": True, "scope": view["scope"], "children_reaped": len(report["domains"])}


if __name__ == "__main__":
    print(json.dumps(asyncio.run(verify())))
