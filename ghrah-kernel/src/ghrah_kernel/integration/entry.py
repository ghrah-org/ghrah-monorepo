# SPDX-FileCopyrightText: 2026 chenxya <chenxya@ghrah.org>
#
# SPDX-License-Identifier: Apache-2.0

"""真实受管入口；业务请求不能选择这里运行的服务实现。"""

import argparse
import asyncio
import json
from multiprocessing.connection import Connection

from ..client import RpcClient
from ..endpoint import AsyncEndpoint
from ..kernel_client import KernelClient
from ..provider import Provider
from ..wire import MessageConnection
from .contracts import NAMESPACE_SCHEMES
from .identity import StaticIdentityConfig
from .namespace_provider import NamespaceProvider


async def run_service(
    provider_fd: int, caller_fd: int, credential: str, enrollment: str, configuration: dict
) -> None:
    """显式装配服务，正常退出时先关闭宿主资源再关闭两侧端点。"""
    caller = KernelClient(RpcClient(AsyncEndpoint(MessageConnection(Connection(caller_fd)))))
    endpoint = AsyncEndpoint(MessageConnection(Connection(provider_fd)))
    agent = None
    try:
        identities = StaticIdentityConfig.from_dict(configuration["identities"])
        await caller.hello(credential, requires_ns_root=False)
        if configuration["provider"] == "namespace":
            service = NamespaceProvider(identities, configuration["administrator"])
            schemes = NAMESPACE_SCHEMES
        elif configuration["provider"] == "agent":
            from .agent_provider import AGENT_SCHEMES, AgentProvider, create_resources

            run_id = caller.session["domain_id"]
            agent = AgentProvider(
                configuration, await create_resources(configuration, run_id), run_id
            )
            service, schemes = agent, AGENT_SCHEMES
        else:
            raise ValueError("没有声明该受管服务实现")
        await Provider(
            endpoint,
            enrollment,
            schemes,
            open_handler=service.open,
            invoke_handler=service.invoke,
        ).serve()
    finally:
        try:
            if agent is not None:
                await agent.aclose()
        finally:
            await caller.aclose()
            await endpoint.aclose()


def main() -> int:
    """读取 Kernel 预建连接和固定服务配置，不接受任意模块加载。"""
    parser = argparse.ArgumentParser(description="ghrah 受管系统服务")
    for name in ("provider-fd", "caller-fd"):
        parser.add_argument(f"--{name}", type=int, required=True)
    for name in ("credential", "enrollment", "config-json"):
        parser.add_argument(f"--{name}", required=True)
    args = parser.parse_args()
    asyncio.run(
        run_service(
            args.provider_fd,
            args.caller_fd,
            args.credential,
            args.enrollment,
            json.loads(args.config_json),
        )
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
