# SPDX-FileCopyrightText: 2026 chenxya <chenxya@ghrah.org>
#
# SPDX-License-Identifier: Apache-2.0

from __future__ import annotations

from ghrah.subject.task.graph import TaskGraphView
from ghrah.subject.task.models import TaskRecord

# ─── 辅助 ───


def _task(
    task_id: str,
    *,
    dependencies: list[str] | None = None,
    parent_id: str | None = None,
) -> TaskRecord:
    return TaskRecord(
        task_id=task_id,
        project_id="proj-1",
        title=task_id.upper(),
        dependencies=list(dependencies or []),
        parent_id=parent_id,
    )


# ─── 邻接查询 ───


class TestAdjacency:
    def test_predecessors(self) -> None:
        # a 依赖 b, c
        graph = TaskGraphView([_task("a", dependencies=["b", "c"]), _task("b"), _task("c")])
        assert graph.predecessors("a") == {"b", "c"}
        assert graph.predecessors("b") == set()
        assert graph.predecessors("c") == set()

    def test_dependents_of(self) -> None:
        # a 依赖 b, c -> b 和 c 的 dependents 都含 a
        graph = TaskGraphView([_task("a", dependencies=["b", "c"]), _task("b"), _task("c")])
        assert graph.dependents_of("b") == {"a"}
        assert graph.dependents_of("c") == {"a"}
        assert graph.dependents_of("a") == set()

    def test_children_of(self) -> None:
        # c 的 parent 是 a
        graph = TaskGraphView([_task("a"), _task("b"), _task("c", parent_id="a")])
        assert graph.children_of("a") == {"c"}
        assert graph.children_of("b") == set()

    def test_unknown_task_id_returns_empty_no_raise(self) -> None:
        graph = TaskGraphView([_task("a"), _task("b")])
        assert graph.dependents_of("ghost") == set()
        assert graph.predecessors("ghost") == set()
        assert graph.children_of("ghost") == set()

    def test_dependency_edge_to_unknown_task_dropped(self) -> None:
        # a 依赖 b 和一个不存在的 ghost -> ghost 边被丢弃
        graph = TaskGraphView([_task("a", dependencies=["b", "ghost"]), _task("b")])
        assert graph.predecessors("a") == {"b"}


# ─── 增量环校验：dependencies ───


class TestDependencyCycle:
    def _chain(self) -> TaskGraphView:
        # a -> b -> c（a 依赖 b，b 依赖 c）
        return TaskGraphView(
            [
                _task("a", dependencies=["b"]),
                _task("b", dependencies=["c"]),
                _task("c"),
            ]
        )

    def test_self_reference_is_cycle(self) -> None:
        graph = self._chain()
        assert graph.would_create_dependency_cycle("a", ["a"]) is True

    def test_back_edge_creates_cycle(self) -> None:
        # 现有 a->b->c；若把 c 的依赖设为含 a，则成环
        graph = self._chain()
        assert graph.would_create_dependency_cycle("c", ["a"]) is True

    def test_no_cycle_passes(self) -> None:
        graph = self._chain()
        assert graph.would_create_dependency_cycle("a", []) is False

    def test_new_redundant_dependency_no_cycle(self) -> None:
        # 现有 a->b->c；设 a 直接依赖 c（a 已间接依赖 c），冗余但不回环
        graph = self._chain()
        assert graph.would_create_dependency_cycle("a", ["c"]) is False

    def test_dependency_on_unknown_task_ignored(self) -> None:
        graph = self._chain()
        # 指向不存在的 task，不构成环
        assert graph.would_create_dependency_cycle("a", ["ghost"]) is False


# ─── 增量环校验：parent ───


class TestParentCycle:
    def test_self_reference_is_cycle(self) -> None:
        graph = TaskGraphView([_task("a"), _task("b")])
        assert graph.would_create_parent_cycle("a", "a") is True

    def test_parent_chain_cycle(self) -> None:
        # 已有 a.parent=b；若把 b.parent 设为 a，成环
        graph = TaskGraphView([_task("a", parent_id="b"), _task("b")])
        assert graph.would_create_parent_cycle("b", "a") is True

    def test_legal_parent_no_cycle(self) -> None:
        # 仅 a.parent=b，设 a.parent=b（不变）无环
        graph = TaskGraphView([_task("a", parent_id="b"), _task("b")])
        assert graph.would_create_parent_cycle("a", "b") is False

    def test_none_parent_no_cycle(self) -> None:
        graph = TaskGraphView([_task("a")])
        assert graph.would_create_parent_cycle("a", None) is False

    def test_deep_parent_chain_reaches_target(self) -> None:
        # 链 c.parent=b, b.parent=a；若设 a.parent=c，沿 c->b->a 回到 a，成环
        graph = TaskGraphView(
            [
                _task("a"),
                _task("b", parent_id="a"),
                _task("c", parent_id="b"),
            ]
        )
        assert graph.would_create_parent_cycle("a", "c") is True


# ─── 全图诊断 ───


class TestHasDependencyCycle:
    def test_acyclic_graph(self) -> None:
        # a->b->c 无环
        graph = TaskGraphView(
            [
                _task("a", dependencies=["b"]),
                _task("b", dependencies=["c"]),
                _task("c"),
            ]
        )
        assert graph.has_dependency_cycle() is False

    def test_cyclic_graph(self) -> None:
        # a->b, b->c, c->a 成环
        graph = TaskGraphView(
            [
                _task("a", dependencies=["b"]),
                _task("b", dependencies=["c"]),
                _task("c", dependencies=["a"]),
            ]
        )
        assert graph.has_dependency_cycle() is True

    def test_empty_graph(self) -> None:
        graph = TaskGraphView([])
        assert graph.has_dependency_cycle() is False
