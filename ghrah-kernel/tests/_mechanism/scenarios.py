# SPDX-FileCopyrightText: 2026 chenxya <chenxya@ghrah.org>
#
# SPDX-License-Identifier: Apache-2.0

"""真实子进程的消息往返与结果收集。"""

import asyncio
import os
import platform
import sys
from contextlib import asynccontextmanager
from dataclasses import asdict, dataclass
from multiprocessing import Pipe
from time import perf_counter

from _mechanism.bootstrap import ENTRY_ENV
from ghrah_kernel.client import RpcClient
from ghrah_kernel.endpoint import AsyncEndpoint
from ghrah_kernel.protocol import PROFILE
from ghrah_kernel.wire import MessageConnection


@dataclass(frozen=True)
class AddArguments:
    a: int
    b: int


@asynccontextmanager
async def open_message_provider():
    parent, child = Pipe(duplex=True)
    process = None
    client = None
    try:
        process = await asyncio.create_subprocess_exec(
            sys.executable,
            "-m",
            "_mechanism.provider_main",
            "--fd",
            str(child.fileno()),
            pass_fds=(child.fileno(),),
            env=ENTRY_ENV,
        )
        child.close()
        client = RpcClient(AsyncEndpoint(MessageConnection(parent)))
        yield client, process
    finally:
        child.close()
        try:
            if client is not None:
                await client.aclose()
            else:
                parent.close()
        finally:
            if process is not None:
                try:
                    await asyncio.wait_for(process.wait(), timeout=2)
                except TimeoutError:
                    if process.returncode is None:
                        process.kill()
                    await process.wait()


async def run_message_roundtrip() -> dict:
    started = perf_counter()
    async with open_message_provider() as (client, process):
        identity = await client.call("identity", {})
        addition = await client.call("add", AddArguments(1, 2))
        business = {"action": "业务字段", "rights": ["不作为权限"], "nested": {"值": 42}}
        echoed = await client.call("echo", business)
        large = {"text": "x" * 40_000}
        large_echo = await client.call("echo", large)
        passed = (
            identity == {"pid": process.pid, "parent_pid": os.getpid()}
            and process.pid != os.getpid()
            and addition == {"sum": 3}
            and echoed == business
            and large_echo == large
        )
    passed = passed and process.returncode == 0
    return {
        "profile": PROFILE,
        "scope": "高层消息骨架；尚无内核 ABI、句柄或 Supervisor",
        "passed": passed,
        "parent_pid": os.getpid(),
        "provider": identity,
        "child_exit_code": process.returncode,
        "dataclass_result": addition,
        "structured_echo": echoed,
        "large_message_characters": len(large_echo["text"]),
        "elapsed_ms": round((perf_counter() - started) * 1000, 2),
        "python": platform.python_version(),
        "platform": platform.platform(),
    }


async def run_service_chain() -> dict:
    from ghrah_kernel.client import RemoteError

    from .bootstrap import open_service_chain

    started = perf_counter()
    async with open_service_chain() as host:
        client = host.observer
        agent = await client.open("/scheme/agent/demo")
        try:
            business = {"action": "业务参数", "rights": ["不作为授权"]}
            result = await client.call(
                agent,
                "run",
                {
                    "steps": [
                        {"action": "add", "arguments": {"a": 1, "b": 2}},
                        {"action": "echo", "arguments": business},
                    ]
                },
            )
            probe = await client.call(agent, "attempt_register", {})
        finally:
            await client.close(agent)
        old = await client.open("/scheme/ability/main")
        identity = await client.call(old, "identity", {})
        previous = host.services["tools"]
        ended = await host.control.kill(previous["domain_id"])
        stale = None
        try:
            await client.invoke(old, "add", {"a": 1, "b": 2})
        except RemoteError as exc:
            stale = exc.code
        await client.close(old)
        replacement = await host.start_service("tools", "ability")
        await host.mount("/scheme/ability/main", replacement, "ability")
        new = await client.open("/scheme/ability/main")
        try:
            fresh = await client.call(new, "add", {"a": 3, "b": 4})
        finally:
            await client.close(new)
        kernel_pid = host.process.pid
        passed = (
            result["outputs"] == [{"sum": 3}, business]
            and result["agent_parent_pid"] == kernel_pid
            and identity["parent_pid"] == kernel_pid
            and probe["error_code"] == "ACCESS_DENIED"
            and ended["reaped"]
            and stale == "STALE_REFERENCE"
            and replacement["domain_generation"] == previous["domain_generation"] + 1
            and fresh == {"sum": 7}
        )
    final = host.final_report
    passed = passed and final["stats"]["domains_live"] == 0 and not final["errors"]
    return {
        "profile": PROFILE,
        "scope": "六个基础入口服务链演示；许可机制另见 references 场景",
        "passed": passed,
        "kernel_pid": kernel_pid,
        "agent_run": result,
        "ability_identity": identity,
        "guest_registration": probe,
        "old_handle_result": stale,
        "generations": [previous["domain_generation"], replacement["domain_generation"]],
        "fresh_result": fresh,
        "kernel_exit_code": host.process.returncode,
        "final": final,
        "elapsed_ms": round((perf_counter() - started) * 1000, 2),
        "python": platform.python_version(),
    }


async def run_reference_chain() -> dict:
    from ghrah_kernel.client import RemoteError

    from .bootstrap import open_service_chain

    started = perf_counter()
    async with open_service_chain(peer_accepts=True) as host:
        source, target, admin = host.observer, host.peer, host.control
        parent = await source.open("/scheme/ability/main")
        receipt = await source.dup(
            parent, actions=["echo"], target_session=target.session["session_id"]
        )
        installed = await target.wait_event()
        await source.close(parent)
        invocation = await target.invoke(receipt["handle_id"], "echo", {"secret": 42})
        before = await target.wait(invocation)
        revoked = await source.revoke({"type": "handle", "handle": parent})
        after = await target.wait(invocation)

        handle = await source.open("/scheme/ability/main/none")
        pending = await source.invoke(
            handle,
            "delay",
            {
                "gate": "demo-cancel",
                "ignore_cancel": True,
                "value": {"late": True},
            },
        )
        root = host.services["tools"]["scheme_handles"]["ability"]
        async with asyncio.timeout(2):
            while "demo-cancel" not in (await admin.call(root, "probe", {}))["active_calls"]:
                await asyncio.sleep(0.002)
        await source.cancel(pending)
        cancelled = await source.wait(pending)
        await admin.call(root, "release", {"gate": "demo-cancel"})
        async with asyncio.timeout(2):
            while (await admin.stats())["late_replies"] == 0:
                await asyncio.sleep(0.002)
        still_cancelled = await source.wait(pending)
        expired = await source.invoke(handle, "counter", {}, deadline=0)
        timed_out = await source.wait(expired)
        selector = {
            "type": "basis",
            "scheme_id": host.services["tools"]["scheme_refs"]["ability"]["scheme_id"],
            "grant_id": "none",
            "revision": 1,
        }
        await admin.revoke(selector)
        replay = None
        try:
            await source.open("/scheme/ability/main/none")
        except RemoteError as exc:
            replay = exc.code
        passed = (
            installed["handle_id"] == receipt["handle_id"]
            and receipt["depth_left"] == 0
            and before.value == {"secret": 42}
            and after.value is None
            and after.settlement_state == before.settlement_state == "COMPLETED"
            and after.delivery_status == "REVOKED"
            and cancelled.settlement_state == "CANCELLED"
            and cancelled.effect_evidence == "UNKNOWN"
            and still_cancelled == cancelled
            and timed_out.settlement_state == "TIMED_OUT"
            and timed_out.dispatch_fact == "NOT_DISPATCHED"
            and replay == "REVOKED"
        )
    final = host.final_report
    passed = (
        passed
        and not final["errors"]
        and host.process.returncode == 0
        and all(
            final["stats"][key] == 0
            for key in (
                "domains_live",
                "sessions_active",
                "handles",
                "objects",
                "invocations",
                "inflight",
            )
        )
    )
    return {
        "profile": PROFILE,
        "scope": "可信许可、跨会话派生、撤权与有限取消结算",
        "passed": passed,
        "installation": installed,
        "revocation": revoked,
        "before_revoke": asdict(before),
        "after_revoke": asdict(after),
        "cancelled": asdict(cancelled),
        "late_reply_did_not_change_terminal": still_cancelled == cancelled,
        "deadline": asdict(timed_out),
        "revoked_basis_reopen": replay,
        "kernel_exit_code": host.process.returncode,
        "final": final,
        "elapsed_ms": round((perf_counter() - started) * 1000, 2),
        "python": platform.python_version(),
    }


async def run_event_stream_chain() -> dict:
    import hashlib

    from .bootstrap import open_service_chain

    started = perf_counter()
    text = "0123456789" * 1000
    digest = hashlib.sha256(text.encode()).hexdigest()
    async with open_service_chain() as host:
        agent = await host.observer.open("/scheme/agent/demo")
        try:
            result = await host.observer.call(
                agent,
                "run",
                {
                    "steps": [
                        {"action": "add", "arguments": {"a": 20, "b": 22}},
                        {"kind": "event", "value": {"from": "agent", "rights": ["业务内容"]}},
                        {"kind": "stream", "direction": "upload", "text": text},
                        {"kind": "stream", "direction": "download", "text": text},
                        {"kind": "timer", "milliseconds": 5},
                        {"action": "missing", "arguments": {}, "expected_error": "ACCESS_DENIED"},
                    ]
                },
            )
        finally:
            await host.observer.close(agent)
        outputs = result["outputs"]
        passed = (
            outputs[0] == {"sum": 42}
            and outputs[1] == {"from": "agent", "rights": ["业务内容"]}
            and all(
                x["sha256"] == digest and x["bytes"] == 10000 and x["eof"] and x["short_writes"] > 0
                for x in outputs[2:4]
            )
            and outputs[4]["status"] == "FIRED"
            and outputs[5] == {"error_code": "ACCESS_DENIED"}
            and result["agent_parent_pid"] == host.process.pid
        )
    final = host.final_report
    passed = (
        passed
        and not final["errors"]
        and host.process.returncode == 0
        and all(
            final["stats"][key] == 0
            for key in (
                "domains_live",
                "sessions_active",
                "handles",
                "objects",
                "invocations",
                "inflight",
                "event_records",
                "pending_subscriptions",
                "queued_events",
                "channels",
                "pipe_fds",
                "io_waiters",
                "event_waiters",
            )
        )
    )
    return {
        "profile": PROFILE,
        "scope": "Agent 消费事件、定时器与双向选择的 OS pipe 单向流",
        "passed": passed,
        "agent_run": result,
        "kernel_exit_code": host.process.returncode,
        "final": final,
        "elapsed_ms": round((perf_counter() - started) * 1000, 2),
        "python": platform.python_version(),
    }


async def run_supervision_chain() -> dict:
    from dataclasses import replace

    from ghrah_kernel.client import RemoteError
    from ghrah_kernel.supervisor import ServiceSpec

    from .bootstrap import open_supervised_chain
    from .contracts import DEFAULT_SERVICES

    started = perf_counter()
    services = (
        DEFAULT_SERVICES[0],
        replace(DEFAULT_SERVICES[1], fixture="ignore_term", timeout_action="kill"),
        ServiceSpec("once", "ability", "never", (("/scheme/ability/once", "ability"),)),
        DEFAULT_SERVICES[2],
    )
    async with open_supervised_chain(services) as host:
        client, supervisor = host.observer, host.supervisor
        agent = await client.open("/scheme/agent/demo")
        agent_result = await client.call(
            agent, "run", {"steps": [{"action": "add", "arguments": {"a": 1, "b": 2}}]}
        )
        await client.close(agent)

        async def exit_provider(handle):
            invocation = await client.invoke(handle, "fault", {"mode": "exit", "code": 17})
            result = await client.wait(invocation)
            await client.close(invocation)
            return result

        once = await client.open("/scheme/ability/once")
        await exit_provider(once)
        never = await supervisor.wait_for("once", lambda x: x["phase"] == "Exited")
        await client.close(once)

        old = await client.open("/scheme/ability/main")
        first = supervisor.view("tools")["kernel"]
        await exit_provider(old)
        second = await supervisor.wait_for(
            "tools", lambda x: x["phase"] == "Running" and x["kernel"]["domain_generation"] == 2
        )
        stale = None
        try:
            await client.invoke(old, "identity", {})
        except RemoteError as exc:
            stale = exc.code
        await client.close(old)
        spin = await client.open("/scheme/ability/main")
        invocation = await client.invoke(spin, "fault", {"mode": "spin"}, timeout_ms=50)
        timeout = await client.wait(invocation)
        third = await supervisor.wait_for(
            "tools", lambda x: x["phase"] == "Running" and x["kernel"]["domain_generation"] == 3
        )
        forced = await host.control.status(second["kernel"]["domain_id"])
        await client.close(invocation)
        await client.close(spin)
        fresh = await client.open("/scheme/ability/main")
        fresh_result = await client.call(fresh, "add", {"a": 3, "b": 4})
        identity = await client.call(fresh, "identity", {})
        await client.close(fresh)

        worker = supervisor.view("worker")["kernel"]
        await host.control.kill(worker["domain_id"])
        await supervisor.wait_for(
            "worker", lambda x: x["phase"] == "Running" and x["kernel"]["domain_generation"] == 2
        )
        explicitly_stopped = await supervisor.stop_service("worker")
        await supervisor.reconcile()
        passed = (
            never["restart_count"] == 0
            and never["kernel"]["exit_code"] == 17
            and stale == "STALE_REFERENCE"
            and first["domain_generation"] == 1
            and third["restart_count"] == 2
            and forced["reaped"]
            and forced["forced"]
            and forced["exit_code"] == -9
            and timeout.policy_action_required
            and timeout.effect_evidence == "UNKNOWN"
            and fresh_result == {"sum": 7}
            and identity["parent_pid"] == host.process.pid
            and explicitly_stopped["desired"] == "stopped"
            and explicitly_stopped["phase"] == "Stopped"
            and agent_result["outputs"] == [{"sum": 3}]
        )
    final = host.final_report
    passed = (
        passed
        and host.process.returncode == 0
        and not final["errors"]
        and not final["unhandled_policy"]
        and all(
            final["stats"][key] == 0
            for key in (
                "domains_live",
                "sessions_active",
                "handles",
                "objects",
                "invocations",
                "inflight",
                "event_records",
                "pending_subscriptions",
                "queued_events",
                "channels",
                "pipe_fds",
                "io_waiters",
                "event_waiters",
                "supervision_waiters",
                "policy_requests",
            )
        )
    )
    return {
        "profile": PROFILE,
        "scope": "策略型 Supervisor 与唯一父进程 Kernel 的真实重启/回收链",
        "passed": passed,
        "never": never,
        "generations": [1, 2, 3],
        "old_handle": stale,
        "timeout": asdict(timeout),
        "forced_instance": forced,
        "fresh_result": fresh_result,
        "always_then_explicit_stop": explicitly_stopped,
        "history": list(supervisor.history),
        "kernel_exit_code": host.process.returncode,
        "final": final,
        "elapsed_ms": round((perf_counter() - started) * 1000, 2),
        "python": platform.python_version(),
    }
