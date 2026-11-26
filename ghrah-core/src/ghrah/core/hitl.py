# SPDX-FileCopyrightText: 2026 chenxya <chenxya@ghrah.org>
#
# SPDX-License-Identifier: Apache-2.0

"""HITL（Human-in-the-Loop）结果接收机制。

提供 Future-based 的 HITL 结果等待和解析，用于单体模式下快速构建HITL型审批。

核心流程：
1. Agent 驱动循环遇到需要 HITL 的操作时，生成 promise_id 并创建 asyncio.Future
2. 发布 HITLRequestEvent（携带 promise_id）到 客户端
3. Observer 审批后凭 promise_id 回发 hitl_response，Core 经
   HITLPromiseRegistry 翻译回三元组并 resolve 对应 Future，驱动循环继续

线程安全：所有操作在 asyncio 事件循环中执行。
"""

from __future__ import annotations

import asyncio
import logging
from dataclasses import dataclass
from typing import Any

logger = logging.getLogger(__name__)

__all__ = ["HITLResult", "HITLFutureStore", "HITLPromiseRegistry"]


@dataclass
class HITLResult:
    """HITL 审批结果。

    Attributes:
        approved: 是否批准
        result: 审批附加结果（如修改后的参数、拒绝原因等）
    """

    approved: bool
    result: Any = None


class HITLFutureStore:
    """HITL Future 存储。

    管理 (agent_name, ability_name, tool_call_id) → asyncio.Future 的映射。
    当 Core Server 路由回 HITL Response 时，通过此存储 resolve 对应的 Future。

    用法::

        store = HITLFutureStore()

        # Agent 侧：创建 Future 等待 HITL 结果
        future = store.create_future("my-agent", "write_file", "call_123")

        # ... 发布 HITLRequestEvent，等待审批 ...

        # Core Server 侧：收到审批结果后 resolve Future
        store.resolve_future("my-agent", "write_file", "call_123", HITLResult(approved=True))

        # Agent 侧：Future 被解析，驱动循环继续
        result = await future
        assert result.approved is True
    """

    def __init__(self) -> None:
        # key: (agent_name, ability_name, tool_call_id)
        self._futures: dict[tuple[str, str, str], asyncio.Future[HITLResult]] = {}

    def create_future(
        self,
        agent_name: str,
        ability_name: str,
        tool_call_id: str,
    ) -> asyncio.Future[HITLResult]:
        """创建一个 HITL 等待 Future。

        如果同一 key 已存在 Future，则取消旧 Future 并创建新的。

        Args:
            agent_name: Agent 名称
            ability_name: Ability 名称
            tool_call_id: 工具调用 ID（用于区分同一 ability 的多次调用）

        Returns:
            等待 HITL 结果的 Future
        """
        key = (agent_name, ability_name, tool_call_id)
        if key in self._futures:
            logger.warning(f"HITL future already exists for {key}, replacing")
            old_future = self._futures[key]
            if not old_future.done():
                old_future.cancel()

        future: asyncio.Future[HITLResult] = asyncio.get_running_loop().create_future()
        self._futures[key] = future
        logger.debug(f"Created HITL future for {key}")
        return future

    def resolve_future(
        self,
        agent_name: str,
        ability_name: str,
        tool_call_id: str,
        result: HITLResult,
    ) -> bool:
        """解析 HITL Future。

        Args:
            agent_name: Agent 名称
            ability_name: Ability 名称
            tool_call_id: 工具调用 ID
            result: HITL 审批结果

        Returns:
            是否成功解析（False 表示 Future 不存在或已完成）
        """
        key = (agent_name, ability_name, tool_call_id)
        future = self._futures.get(key)
        if future is None:
            logger.warning(f"No HITL future found for {key}")
            return False

        if future.done():
            logger.warning(f"HITL future already resolved for {key}")
            return False

        future.set_result(result)
        del self._futures[key]
        logger.info(f"Resolved HITL future for {key}: approved={result.approved}")
        return True

    def get_future(
        self,
        agent_name: str,
        ability_name: str,
        tool_call_id: str,
    ) -> asyncio.Future[HITLResult] | None:
        """获取 HITL Future（不创建新的）。

        Args:
            agent_name: Agent 名称
            ability_name: Ability 名称
            tool_call_id: 工具调用 ID

        Returns:
            对应的 Future，如果不存在则返回 None
        """
        key = (agent_name, ability_name, tool_call_id)
        return self._futures.get(key)

    def cancel_future(
        self,
        agent_name: str,
        ability_name: str,
        tool_call_id: str,
    ) -> bool:
        """取消指定的 HITL Future。

        Args:
            agent_name: Agent 名称
            ability_name: Ability 名称
            tool_call_id: 工具调用 ID

        Returns:
            是否成功取消
        """
        key = (agent_name, ability_name, tool_call_id)
        future = self._futures.get(key)
        if future is None:
            return False

        if not future.done():
            future.cancel()
        del self._futures[key]
        logger.debug(f"Cancelled HITL future for {key}")
        return True

    def cancel_all(self, agent_name: str | None = None) -> None:
        """取消所有等待中的 Future。

        Args:
            agent_name: 如果指定，只取消该 Agent 的 Future
        """
        keys_to_remove = []
        for key, future in self._futures.items():
            if agent_name is None or key[0] == agent_name:
                if not future.done():
                    future.cancel()
                keys_to_remove.append(key)

        for key in keys_to_remove:
            del self._futures[key]

        if keys_to_remove:
            logger.info(
                f"Cancelled {len(keys_to_remove)} HITL futures"
                f"{' for agent ' + agent_name if agent_name else ''}"
            )

    def list_pending(self, agent_name: str | None = None) -> list[tuple[str, str, str]]:
        """列出等待中的 HITL Future key。

        Args:
            agent_name: 如果指定，只列出该 Agent 的 Future

        Returns:
            Future key 列表
        """
        if agent_name is not None:
            return [k for k in self._futures if k[0] == agent_name]
        return list(self._futures.keys())


class HITLPromiseRegistry:
    """HITL promise 注册表：promise_id → (agent_name, ability_name, tool_call_id)。

    hitl_request 事件携带 Core 生成的 promise_id 下发 Observer；Observer 的
    hitl_response 仅凭 promise_id 回程，CoreUnit 据此表翻译回三元组并 resolve
    HITLFutureStore。超时/已 resolve 的 promise 由 resolve/remove 惰性清除。
    """

    def __init__(self) -> None:
        # promise_id → (agent_name, ability_name, tool_call_id)
        self._promises: dict[str, tuple[str, str, str]] = {}

    def register(
        self,
        promise_id: str,
        agent_name: str,
        ability_name: str,
        tool_call_id: str,
    ) -> None:
        """登记一个 pending promise。

        同一 promise_id 重复登记时覆盖并告警（正常流程 uuid 不会重复）。

        Args:
            promise_id: promise 标识
            agent_name: Agent 名称
            ability_name: Ability 名称
            tool_call_id: 工具调用 ID
        """
        if promise_id in self._promises:
            logger.warning(f"HITL promise already registered for '{promise_id}', replacing")
        self._promises[promise_id] = (agent_name, ability_name, tool_call_id)

    def lookup(self, promise_id: str) -> tuple[str, str, str] | None:
        """查询待审批路由，保留它直至审批完成。"""
        return self._promises.get(promise_id)

    def lookup_promise(self, agent_name: str, ability_name: str, tool_call_id: str) -> str | None:
        """为兼容三元组回包定位当前待审批项。"""
        triplet = (agent_name, ability_name, tool_call_id)
        return next((key for key, value in self._promises.items() if value == triplet), None)

    def resolve(self, promise_id: str) -> tuple[str, str, str] | None:
        """取出并移除 promise 对应的三元组。

        Args:
            promise_id: promise 标识

        Returns:
            三元组 (agent_name, ability_name, tool_call_id)；不存在时返回 None
        """
        return self._promises.pop(promise_id, None)

    def remove(self, promise_id: str) -> None:
        """移除一个 promise（不存在时静默）。"""
        self._promises.pop(promise_id, None)

    def remove_triplet(self, agent_name: str, ability_name: str, tool_call_id: str) -> None:
        """按三元组移除对应 promise（超时惰性清理用）。"""
        triplet = (agent_name, ability_name, tool_call_id)
        stale = [pid for pid, t in self._promises.items() if t == triplet]
        for pid in stale:
            del self._promises[pid]

    def clear(self, agent_name: str | None = None) -> None:
        """清空注册表（可按 Agent 清）。

        Args:
            agent_name: 如果指定，只清该 Agent 的 promise
        """
        if agent_name is None:
            self._promises.clear()
            return
        stale = [pid for pid, t in self._promises.items() if t[0] == agent_name]
        for pid in stale:
            del self._promises[pid]

    def list_pending(self, agent_name: str | None = None) -> list[str]:
        """列出 pending promise_id。

        Args:
            agent_name: 如果指定，只列该 Agent 的 promise

        Returns:
            promise_id 列表
        """
        if agent_name is not None:
            return [pid for pid, t in self._promises.items() if t[0] == agent_name]
        return list(self._promises.keys())
