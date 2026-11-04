"""受管服务入口，Provider 与 Caller 分别持有独立连接。"""

import argparse
import asyncio
import signal
from multiprocessing.connection import Connection

from ghrah_kernel.client import RpcClient
from ghrah_kernel.endpoint import AsyncEndpoint
from ghrah_kernel.kernel_client import KernelClient
from ghrah_kernel.wire import MessageConnection

from .contracts import SERVICE_SCHEMES
from .permission_fixtures import PermissionFixtures
from .provider import ServiceProvider


async def run_service(kind, provider_fd, caller_fd, credential, enrollment, fixture="normal"):
    if fixture in ("ignore_term", "spin"):
        signal.signal(signal.SIGTERM, signal.SIG_IGN)
    caller = KernelClient(RpcClient(AsyncEndpoint(MessageConnection(Connection(caller_fd)))))
    provider = AsyncEndpoint(MessageConnection(Connection(provider_fd)))
    stream_task = None
    try:
        await caller.hello(credential, requires_ns_root=False, accept_channels=kind == "ability")
        resolver = None
        fixtures = PermissionFixtures()
        if kind == "namespace":
            from .mocks.namespace import NamespaceMock

            namespace = NamespaceMock()
            handlers, resolver = {"ns": {"bind": namespace.bind}}, namespace.resolve
        elif kind == "ability":
            from .mocks.ability import AbilityMock

            ability = AbilityMock(fixtures)
            handlers = ability.handlers()
        elif kind == "agent":
            from .mocks.agent import AgentMock

            agent = AgentMock(caller)
            handlers = {
                "agent": {
                    "run": agent.run,
                    "status": agent.status,
                    "attempt_register": agent.attempt_register,
                }
            }
        else:
            raise ValueError("未知服务种类")
        sdk = ServiceProvider(
            provider,
            enrollment,
            SERVICE_SCHEMES[kind],
            handlers,
            resolver=resolver,
            fixtures=fixtures,
            caller=caller,
        )
        if kind == "ability":
            from .mocks.stream_peer import StreamPeer

            ability.publisher = sdk.publish
            ability.disconnect = provider.aclose
            ability.stream_peer = StreamPeer(caller)
            stream_task = asyncio.create_task(ability.stream_peer.receive())
            if fixture in ("exit", "spin"):
                sdk.after_bind = lambda: ability.fault({"mode": fixture})
        try:
            await sdk.serve()
        except ConnectionError:
            if not fixtures.endpoint_dropped:
                raise
        if fixtures.endpoint_dropped:
            # 故障替身刻意保留活进程，验证端点损坏后的 Supervisor 回收策略。
            await asyncio.Event().wait()
    finally:
        if stream_task is not None:
            stream_task.cancel()
            await asyncio.gather(stream_task, return_exceptions=True)
        await caller.aclose()
        await provider.aclose()


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--kind", required=True)
    parser.add_argument("--fixture", default="normal")
    for name in ("provider-fd", "caller-fd"):
        parser.add_argument(f"--{name}", type=int, required=True)
    for name in ("credential", "enrollment"):
        parser.add_argument(f"--{name}", required=True)
    args = parser.parse_args()
    asyncio.run(
        run_service(
            args.kind,
            args.provider_fd,
            args.caller_fd,
            args.credential,
            args.enrollment,
            args.fixture,
        )
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
