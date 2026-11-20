# SPDX-FileCopyrightText: 2026 chenxya <chenxya@ghrah.org>
#
# SPDX-License-Identifier: Apache-2.0
"""成员绑定状态的读写接缝。

绑定的权威是实例快照（``agent_instances.members[].axes``）：工具面只持内存
镜像，任何绑定/解绑都必须经这里写回数据库，重启时再从快照恢复。把这个接缝
做成独立对象，是为了让授权装配边界（``StartupGrantMaterializer``）不必知道
Core 的实例库细节。
"""

from __future__ import annotations

from collections.abc import Callable, Collection
from typing import Any

__all__ = ["MemberBindingState"]


class MemberBindingState:
    """把"哪些成员绑定在模型上下文"落到实例快照。

    Args:
        store_provider: 返回已启动的 ``AgentInstanceStore`` 的同步取值函数；
            延迟调用，避免装配期强制要求实例库已打开。
    """

    def __init__(self, store_provider: Callable[[], Any]) -> None:
        self._store_provider = store_provider

    async def save(self, agent_id: str, bound: Collection[str]) -> None:
        """把绑定集合写回该实例的成员快照。

        Args:
            agent_id: 实例标识。
            bound: 应处于绑定状态的成员全名集合。

        Raises:
            RuntimeError: 实例库未配置或实例不存在（快照是权威，不能凭空写）。
        """
        store = self._store_provider()
        record = await store.set_member_bindings(agent_id, bound)
        if record is None:
            raise RuntimeError(f"agent instance snapshot not found: {agent_id}")
