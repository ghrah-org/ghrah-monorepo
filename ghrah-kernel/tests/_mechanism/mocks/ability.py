"""受管 Ability 替身；业务函数不碰控制信封与 bytes。"""

import asyncio
import os
import time


class AbilityMock:
    def __init__(self, fixtures):
        self.count = 0
        self.fixtures = fixtures
        self.publisher = None
        self.stream_peer = None
        self.disconnect = None

    def add(self, arguments):
        a, b = arguments["a"], arguments["b"]
        if type(a) is not int or type(b) is not int:
            raise ValueError("add 需要两个整数")
        return {"sum": a + b}

    def echo(self, arguments):
        return arguments

    def identity(self, arguments):
        return {"pid": os.getpid(), "parent_pid": os.getppid()}

    async def delay(self, arguments):
        label = arguments.get("gate", "timed")
        self.fixtures.active_calls.add(label)
        end = time.monotonic() + arguments.get("milliseconds", 100) / 1000
        try:
            while True:
                try:
                    if "gate" in arguments:
                        await self.fixtures.gate(label).wait()
                    else:
                        await asyncio.sleep(max(0, end - time.monotonic()))
                    return arguments.get("value")
                except asyncio.CancelledError:
                    if not arguments.get("ignore_cancel", False):
                        raise
        finally:
            self.fixtures.active_calls.discard(label)

    async def counter(self, arguments):
        self.count += 1
        if "gate" in arguments:
            await self.delay(arguments)
        return {"count": self.count}

    def probe(self, arguments):
        return {**self.fixtures.probe(arguments), "count": self.count}

    def release(self, arguments):
        return self.fixtures.release(arguments)

    async def emit(self, arguments):
        return await self.publisher(arguments)

    async def stream(self, arguments):
        return await self.stream_peer.command(arguments)

    def fault(self, arguments):
        mode = arguments["mode"]
        if mode == "drop_endpoint":
            self.fixtures.endpoint_dropped = True
            return self.disconnect()
        if mode == "exit":
            code = arguments.get("code", 17)
            if type(code) is not int or not 0 <= code <= 127:
                raise ValueError("退出码范围为 0..127")
            os._exit(code)
        if mode == "spin":
            while True:
                pass
        raise ValueError("未知故障模式")

    def handlers(self):
        return {
            "ability": {
                name: getattr(self, name)
                for name in (
                    "add",
                    "echo",
                    "identity",
                    "delay",
                    "counter",
                    "probe",
                    "release",
                    "emit",
                    "stream",
                    "fault",
                )
            },
            "view": {"identity": self.identity},
        }
