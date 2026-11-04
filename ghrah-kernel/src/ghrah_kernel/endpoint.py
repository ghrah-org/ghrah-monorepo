"""将阻塞消息 API 接入 asyncio；端点持有固定收发工作者。"""

import asyncio
from concurrent.futures import ThreadPoolExecutor

from .wire import MessageConnection


def _observe(future: asyncio.Future) -> None:
    if not future.cancelled():
        future.exception()


class AsyncEndpoint:
    def __init__(self, wire: MessageConnection):
        self.wire = wire
        self._reader = ThreadPoolExecutor(max_workers=1, thread_name_prefix="lab-recv")
        self._writer = ThreadPoolExecutor(max_workers=1, thread_name_prefix="lab-send")
        self._send_slots = asyncio.Semaphore(16)
        self._receiving: asyncio.Future | None = None
        self._closing = False
        self._close_task: asyncio.Task | None = None

    async def send(self, data: bytes, *, prepare=None) -> None:
        await self._send_slots.acquire()
        if self._closing:
            self._send_slots.release()
            raise ConnectionError("端点已关闭")
        try:
            # 最终门禁在排队之后运行，与交给发送工作者之间没有 await。
            if prepare is not None:
                data = prepare()
            future = asyncio.get_running_loop().run_in_executor(self._writer, self.wire.send, data)
        except BaseException:
            self._send_slots.release()
            raise

        def finished(result: asyncio.Future) -> None:
            self._send_slots.release()
            _observe(result)

        future.add_done_callback(finished)
        # 取消业务等待不能截断已经开始的完整消息发送。
        await asyncio.shield(future)

    async def receive(self) -> bytes:
        if self._closing:
            raise ConnectionError("端点已关闭")
        if self._receiving is not None and not self._receiving.done():
            raise RuntimeError("每个端点只能有一个接收者")
        self._receiving = asyncio.get_running_loop().run_in_executor(
            self._reader, self.wire.receive
        )
        self._receiving.add_done_callback(_observe)
        return await asyncio.shield(self._receiving)

    async def aclose(self) -> None:
        if self._close_task is None:
            self._closing = True
            self._close_task = asyncio.create_task(self._finish_close())
        await asyncio.shield(self._close_task)

    async def _finish_close(self) -> None:
        # 先使阻塞的 socket 收发退出，工作者结束后再释放原 FD。
        try:
            self.wire.shutdown()
        except BaseException:
            # 无法唤醒时显式失败，不无限 join 或谎称工作者已经退出。
            self._reader.shutdown(wait=False, cancel_futures=True)
            self._writer.shutdown(wait=False, cancel_futures=True)
            self.wire.close()
            raise
        try:
            await asyncio.gather(
                asyncio.to_thread(self._reader.shutdown, wait=True, cancel_futures=True),
                asyncio.to_thread(self._writer.shutdown, wait=True, cancel_futures=True),
            )
        finally:
            self.wire.close()
