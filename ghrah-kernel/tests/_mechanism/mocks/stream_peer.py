"""Ability 的独立 Caller 会话消费通道安装事件，实际走 read/write ABI。"""

import asyncio

from ghrah_kernel.client import RemoteError


class StreamPeer:
    def __init__(self, caller):
        self.caller = caller
        self.channels: dict[str, dict] = {}
        self.changed = asyncio.Event()

    async def receive(self):
        while True:
            try:
                event = await self.caller.wait_event(timeout_ms=1000)
            except RemoteError as exc:
                if exc.code == "WAIT_TIMEOUT":
                    continue
                raise
            if event["type"] == "channel_installed":
                self.channels[event["channel_id"]] = event
                self.changed.set()

    async def command(self, arguments):
        if arguments["op"] == "status":
            return {"channels": list(self.channels)}
        channel = arguments["channel_id"]
        async with asyncio.timeout(1):
            while channel not in self.channels:
                self.changed.clear()
                await self.changed.wait()
        handle = self.channels[channel]["handle_id"]
        if arguments["op"] == "read":
            result = await self.caller.read(
                handle,
                arguments.get("max_bytes", 16384),
                timeout_ms=arguments.get("timeout_ms", 1000),
            )
            return {"hex": result.data.hex(), "eof": result.eof}
        if arguments["op"] == "write":
            count = await self.caller.write(
                handle,
                bytes.fromhex(arguments["hex"]),
                timeout_ms=arguments.get("timeout_ms", 1000),
            )
            return {"accepted_bytes": count}
        if arguments["op"] == "close":
            await self.caller.close(handle)
            self.channels.pop(channel)
            return {"closed": True}
        raise ValueError("未知流控制命令")
