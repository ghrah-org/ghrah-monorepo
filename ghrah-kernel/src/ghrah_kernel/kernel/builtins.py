"""内核自有契约解释自己的系统参数，不解释用户态业务字典。"""

import asyncio

from ..abi import ABI_ENTRIES
from ..payloads import decode_payload, encode_payload
from .tables import KernelFault


async def invoke_builtin(kernel, session, scheme, action, payload, source):
    arguments = decode_payload(payload)
    if scheme == "kernel.sys":
        result = kernel.stats()
        result["implemented_abi"] = list(ABI_ENTRIES)
    elif scheme == "kernel.time":
        if action == "now":
            result = {"monotonic": asyncio.get_running_loop().time()}
        elif action == "create_timer":
            result = kernel.events.create_timer(session, source, arguments["milliseconds"])
        elif action == "cancel_timer":
            result = kernel.events.cancel_timer(session, arguments["handle"])
        else:
            raise KernelFault("NOT_SUPPORTED", "未知定时动作")
    elif scheme == "kernel.proc" and session.role == "bootstrap":
        if action == "spawn":
            result = await kernel.processes.spawn(**arguments)
        elif action == "kill":
            result = await kernel.processes.kill(arguments["domain"])
        elif action == "status":
            result = kernel.processes.status(arguments["domain"])
        elif action == "snapshot":
            result = {
                "domains": [kernel.processes.status(x) for x in kernel.processes.domains],
                "policy_requests": [
                    x for x in kernel.policy_requests.values() if x["status"] == "pending"
                ],
                "revision": kernel.fact_revision,
            }
        elif action == "wait":
            result = await kernel.processes.wait(
                arguments["domain"], arguments.get("timeout_ms", 200)
            )
        elif action == "drain":
            result = await kernel.processes.drain(
                arguments["domain"], arguments.get("timeout_ms", 50)
            )
        elif action == "ack_policy":
            result = kernel.acknowledge_policy(arguments)
        elif action == "retire":
            domain = kernel.processes.get(arguments["domain"])
            domain.state = "stopped"
            kernel.events.domain_closed(domain.domain_id)
            kernel.streams.domain_closed(domain.domain_id)
            result = domain.snapshot()
        else:
            raise KernelFault("NOT_SUPPORTED", "未知系统动作")
    else:
        raise KernelFault("ACCESS_DENIED", "无系统管理资格")
    effectful = (scheme == "kernel.proc" and action not in ("status", "snapshot", "wait")) or (
        scheme == "kernel.time" and action != "now"
    )
    effect = "PROVIDER_REPORTED_EFFECT" if effectful else "PROVIDER_REPORTED_NO_EFFECT"
    return encode_payload(result), effect
