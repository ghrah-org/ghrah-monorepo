# SPDX-FileCopyrightText: 2026 chenxya <chenxya@ghrah.org>
#
# SPDX-License-Identifier: Apache-2.0

"""真实子进程中的服务端口夹具；不冒充 ActorAgent 或 Ability Provider。"""

import argparse
import asyncio
import json
import os
from multiprocessing.connection import Connection

from ghrah_kernel.client import RpcClient
from ghrah_kernel.endpoint import AsyncEndpoint
from ghrah_kernel.integration.identity import StaticIdentityConfig
from ghrah_kernel.kernel_client import KernelClient
from ghrah_kernel.provider import Provider, ProviderFault, ProviderResult
from ghrah_kernel.wire import MessageConnection

SCHEMES = [
    {
        "name": "probe",
        "contract": "runtime.probe/e0",
        "actions": ["context", "delay", "effect", "fail", "outbound"],
    }
]


async def run(args):
    configuration = json.loads(args.config_json)
    identities = StaticIdentityConfig.from_dict(configuration["identities"])
    caller = KernelClient(RpcClient(AsyncEndpoint(MessageConnection(Connection(args.caller_fd)))))
    endpoint = AsyncEndpoint(MessageConnection(Connection(args.provider_fd)))
    objects = set()
    serial = 0

    def opened(context, path):
        nonlocal serial
        try:
            grant = identities.grant(
                context.principal, configuration["namespace"], configuration["resource"]
            )
        except ValueError as exc:
            raise ProviderFault("ACCESS_DENIED", str(exc), effect="NO_EFFECT") from exc
        serial += 1
        object_id = f"opened:{serial}"
        objects.add(object_id)
        return {
            "result": "opened",
            "provider_object_id": object_id,
            "actions": SCHEMES[0]["actions"],
            "grant": grant,
        }

    async def invoke(context, action, arguments):
        if context.provider_object_id not in objects and context.provider_object_id != "root":
            raise ValueError("未知对象")
        result = {
            **context.to_dict(),
            "pid": os.getpid(),
            "parent_pid": os.getppid(),
            "label": configuration["label"],
            "payload": arguments,
            "service_principal": caller.session["principal"],
            "service_domain": caller.session["domain_id"],
            "launch_environment": os.environ.get("GHRAH_RUNTIME_PROBE"),
        }
        if action == "outbound":
            handle = await caller.open("/service")
            try:
                result["downstream"] = await caller.call(handle, "context", {})
            finally:
                await caller.close(handle)
        if action == "fail":
            raise RuntimeError("效果未核定的受控故障")
        if action == "context" and arguments.get("large_reply"):
            result["large_reply"] = "x" * 70000
        if action == "delay":
            await asyncio.sleep(arguments["seconds"])
        return ProviderResult(result, "EFFECT_OCCURRED" if action == "effect" else "NO_EFFECT")

    def close(scheme, object_id):
        objects.discard(object_id)

    try:
        await caller.hello(args.credential, requires_ns_root=False)
        await Provider(
            endpoint,
            args.enrollment,
            SCHEMES,
            open_handler=opened,
            invoke_handler=invoke,
            close_handler=close,
        ).serve()
    finally:
        await caller.aclose()
        await endpoint.aclose()


def main():
    parser = argparse.ArgumentParser()
    for key in ("provider-fd", "caller-fd"):
        parser.add_argument(f"--{key}", type=int, required=True)
    for key in ("credential", "enrollment", "config-json"):
        parser.add_argument(f"--{key}", required=True)
    asyncio.run(run(parser.parse_args()))


if __name__ == "__main__":
    main()
