# SPDX-FileCopyrightText: 2026 chenxya <chenxya@ghrah.org>
#
# SPDX-License-Identifier: Apache-2.0

"""Hook 的唯一执行路径：Agent 循环与本地能力执行共用。

Hook 与 Operation 看到同一份调用对象与服务表：

- 触发点与成员身份决定选中哪些 hook（``HookStore.select(point, member)``）；
- Hook 内的参数读 ``call.arguments``、依赖读 ``services``；
- ``HookResult.modified_context`` 合并进 ``LOOP`` 服务里的循环数据。
"""

from __future__ import annotations

from ghrah.abilities.hook_store import HookStore
from ghrah.ability.execution.execution_services import LOOP, ExecutionServices
from ghrah.ability.execution.hooks import HookPoint, HookResult
from ghrah.ability.execution.invocation import OperationCall
from ghrah.core.exceptions import HookError
from ghrah.types.results import ActionResult

__all__ = ["HookRunner"]


class HookRunner:
    """Run hooks selected from a HookStore."""

    def __init__(self, store: HookStore) -> None:
        self._store = store

    async def run(
        self,
        point: HookPoint,
        call: OperationCall,
        services: ExecutionServices,
        result: ActionResult | None = None,
    ) -> HookResult | None:
        """运行匹配的 hook 并合并结果。

        Args:
            point: 触发点。
            call: 本次调用的值对象。
            services: 执行期服务表。
            result: 触发点的结果（PRE_EXECUTE 时为 None）。

        Returns:
            合并后的 HookResult；没有 hook 命中时为 None。

        Raises:
            HookError: hook 自身抛错时（附带触发点与 hook 类型）。
        """

        merged: HookResult | None = None
        hooks = self._store.select(point, call.member.member)

        for hook in hooks:
            if self._store.is_legacy(hook):
                try:
                    should_fire = await hook.should_trigger(call, services)
                except Exception as e:
                    raise HookError(
                        point.value,
                        f"should_trigger failed for {type(hook).__name__}: {e}",
                    ) from e

                if not should_fire:
                    continue

            try:
                hook_result = await hook.execute(call, services, result)
            except Exception as e:
                raise HookError(
                    point.value,
                    f"execute failed for {type(hook).__name__}: {e}",
                ) from e

            if merged is None:
                merged = hook_result
            else:
                merged = merged.merge(hook_result)

        if merged is not None and merged.modified_context:
            loop = services.get(LOOP)
            if loop is not None:
                loop.update(merged.modified_context)

        return merged
