"""脚本 Agent 通过自己的 Caller 会话消费能力，不直接导入 Ability。"""

import asyncio
import hashlib
import os

from ghrah_kernel.client import RemoteError


class AgentMock:
    def __init__(self, caller):
        self.caller = caller
        self.state = "idle"
        self.completed = 0

    async def run(self, arguments):
        self.state = "running"
        handle = None
        try:
            handle = await self.caller.open(arguments.get("tool_path", "/scheme/ability/main"))
            outputs = []
            for step in arguments["steps"]:
                try:
                    outputs.append(await self.step(handle, step))
                except RemoteError as exc:
                    if step.get("expected_error") != exc.code:
                        raise
                    outputs.append({"error_code": exc.code})
                self.completed += 1
            return {"outputs": outputs, "agent_pid": os.getpid(), "agent_parent_pid": os.getppid()}
        finally:
            try:
                if handle is not None:
                    await self.caller.close(handle)
            finally:
                self.state = "idle"

    async def step(self, handle, step):
        kind = step.get("kind", "call")
        if kind == "call":
            return await self.caller.call(handle, step["action"], step["arguments"])
        if kind == "event":
            subscription = await self.caller.subscribe(handle)
            try:
                await self.caller.wait_item(subscription)
                await self.caller.call(handle, "emit", step["value"])
                return (await self.caller.wait_item(subscription))["value"]
            finally:
                await self.caller.close(subscription)
        if kind == "timer":
            timer = await self.caller.create_timer(step["milliseconds"])
            try:
                return (await self.caller.wait_item(timer, kind="timer"))["event"]
            finally:
                await self.caller.close(timer)
        if kind == "stream":
            return await self.stream(handle, step)
        raise ValueError("未知脚本步骤")

    async def stream(self, handle, step):
        data = step["text"].encode("utf-8")
        if not 1 <= len(data) <= 32000:
            raise ValueError("实验流脚本接受 1..32000 字节文本")
        direction = step["direction"]
        channel = await self.caller.channel(handle, direction=direction)
        local = channel["handle_id"]
        local_closed = False
        short_writes = 0

        async def command(op, **parameters):
            return await self.caller.call(
                handle,
                "stream",
                {
                    "channel_id": channel["channel_id"],
                    "op": op,
                    **parameters,
                },
            )

        async def peer_work():
            nonlocal short_writes
            try:
                if direction == "upload":
                    received = bytearray()
                    while True:
                        result = await command("read")
                        received.extend(bytes.fromhex(result["hex"]))
                        if result["eof"]:
                            return bytes(received)
                offset = 0
                while offset < len(data):
                    chunk = data[offset : offset + 16384]
                    count = (await command("write", hex=chunk.hex()))["accepted_bytes"]
                    short_writes += count < len(chunk)
                    offset += count
            finally:
                await command("close")

        peer = asyncio.create_task(peer_work())
        try:
            if direction == "upload":
                offset = 0
                while offset < len(data):
                    chunk = data[offset : offset + 16384]
                    count = await self.caller.write(local, chunk)
                    short_writes += count < len(chunk)
                    offset += count
                await self.caller.close(local)
                local_closed = True
                received = await peer
            else:
                received = bytearray()
                while True:
                    chunk = await self.caller.read(local)
                    received.extend(chunk.data)
                    if chunk.eof:
                        break
                await peer
                received = bytes(received)
            if received != data:
                raise ValueError("流往返内容不一致")
            return {
                "direction": direction,
                "bytes": len(received),
                "sha256": hashlib.sha256(received).hexdigest(),
                "eof": True,
                "short_writes": short_writes,
            }
        finally:
            if not peer.done():
                peer.cancel()
            await asyncio.gather(peer, return_exceptions=True)
            if not local_closed:
                await self.caller.close(local)

    def status(self, arguments):
        return {"state": self.state, "completed": self.completed}

    async def attempt_register(self, arguments):
        try:
            await self.caller.register("not-a-domain", [], "guest-attempt")
        except RemoteError as exc:
            return {"error_code": exc.code}
        raise RuntimeError("普通 Agent 获得了注册资格")
