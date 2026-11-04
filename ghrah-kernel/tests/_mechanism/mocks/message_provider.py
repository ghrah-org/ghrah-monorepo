"""消息骨架的业务替身，不是完整的 Agent 或 Ability 域。"""

import asyncio
import os
from multiprocessing.connection import Connection

from ghrah_kernel.endpoint import AsyncEndpoint
from ghrah_kernel.wire import MessageConnection

from ..provider import serve


def add(arguments: dict) -> dict:
    a, b = arguments["a"], arguments["b"]
    if type(a) is not int or type(b) is not int:
        raise ValueError("add 只接受两个整数")
    return {"sum": a + b}


def echo(arguments: dict) -> dict:
    return arguments


def identity(arguments: dict) -> dict:
    return {"pid": os.getpid(), "parent_pid": os.getppid()}


async def delay(arguments: dict) -> dict:
    await asyncio.sleep(arguments["milliseconds"] / 1000)
    return arguments["value"]


async def run(fd: int) -> None:
    endpoint = AsyncEndpoint(MessageConnection(Connection(fd)))
    await serve(endpoint, {"add": add, "echo": echo, "identity": identity, "delay": delay})
