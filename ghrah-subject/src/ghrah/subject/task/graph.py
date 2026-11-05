# SPDX-FileCopyrightText: 2026 chenxya <chenxya@ghrah.org>
#
# SPDX-License-Identifier: Apache-2.0

from __future__ import annotations

from ghrah.subject.task.models import TaskRecord

__all__ = ["TaskGraphView"]


class TaskGraphView:
    """只读纯函数拓扑视图：从 list[TaskRecord] 构建邻接表，集中环检测/可达/邻接查询。

    - dependency 边：task -> task.dependencies（DAG，create/update 时增量环校验）
    - parent 边：task.task_id -> task.parent_id（森林，parent 链环校验）
    只产拓扑信息（task_id 集合），不查状态、不碰持久化。
    """

    def __init__(self, tasks: list[TaskRecord]) -> None:
        self._ids: set[str] = {t.task_id for t in tasks}
        # task -> 它依赖谁（正邻接，DAG 边；指向不存在 task 的边丢弃）
        self._deps_out: dict[str, set[str]] = {
            t.task_id: set(t.dependencies) & self._ids for t in tasks
        }
        # task -> 它被谁依赖（反邻接）
        self._deps_in: dict[str, set[str]] = {tid: set() for tid in self._ids}
        for src, outs in self._deps_out.items():
            for dst in outs:
                self._deps_in[dst].add(src)
        # task -> parent（森林，每节点至多一条 parent 边）
        self._parent: dict[str, str | None] = {t.task_id: t.parent_id for t in tasks}

    def dependents_of(self, task_id: str) -> set[str]:
        """谁依赖我（反邻接）。"""
        return set(self._deps_in.get(task_id, set()))

    def children_of(self, parent_id: str) -> set[str]:
        """谁是我的直接子任务（parent_id 反查）。"""
        return {tid for tid, p in self._parent.items() if p == parent_id}

    def predecessors(self, task_id: str) -> set[str]:
        """我依赖谁（正邻接，用于 task_start 的前驱状态校验）。"""
        return set(self._deps_out.get(task_id, set()))

    def would_create_dependency_cycle(self, task_id: str, new_deps: list[str]) -> bool:
        """若把 task_id 的依赖设为 new_deps，是否会形成环。

        自指或在现有图上从 new_deps 可达 task_id 即环。O(E)。
        """
        dep_set = set(new_deps)
        if task_id in dep_set:
            return True
        return bool(dep_set and task_id in self._reachable(self._deps_out, dep_set))

    def would_create_parent_cycle(self, task_id: str, new_parent: str | None) -> bool:
        """若把 task_id 的 parent 设为 new_parent，是否会形成父子环。

        自指或从 new_parent 沿 parent 链向上能到达 task_id 即环。O(深度)。
        """
        if new_parent is None:
            return False
        if new_parent == task_id:
            return True
        cur: str | None = new_parent
        seen: set[str] = set()
        while cur is not None and cur not in seen:
            if cur == task_id:
                return True
            seen.add(cur)
            cur = self._parent.get(cur)
        return False

    def has_dependency_cycle(self) -> bool:
        """全图是否存在 dependency 环（三色 DFS）。"""
        return self._has_cycle(self._deps_out)

    @staticmethod
    def _reachable(adj: dict[str, set[str]], starts: set[str]) -> set[str]:
        seen: set[str] = set()
        stack = list(starts)
        while stack:
            n = stack.pop()
            if n in seen:
                continue
            seen.add(n)
            stack.extend(adj.get(n, set()) - seen)
        return seen

    @staticmethod
    def _has_cycle(adj: dict[str, set[str]]) -> bool:
        white, gray, black = 0, 1, 2
        color: dict[str, int] = {n: white for n in adj}

        def dfs(n: str) -> bool:
            color[n] = gray
            for nxt in adj.get(n, set()):
                if color.get(nxt, white) == gray:
                    return True
                if color.get(nxt, white) == white and dfs(nxt):
                    return True
            color[n] = black
            return False

        return any(color[n] == white and dfs(n) for n in adj)
