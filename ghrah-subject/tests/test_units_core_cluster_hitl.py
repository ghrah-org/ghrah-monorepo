# SPDX-FileCopyrightText: 2026 chenxya <chenxya@ghrah.org>
#
# SPDX-License-Identifier: Apache-2.0

"""``hitl_response`` 回程扇出测试（C3 端口化后的路由找回）。

背景：CoreUnit 不再经 ``mount_unit`` 挂载到宿主 ctx，Observer 的
``hitl_response`` 一度在路由层死掉（``Unknown command``）。修复后
``CoreClusterRegistryUnit`` 认领该命令并扇出到所有已启动单元，由持有
promise 的单元认领（「返回 None = 不是我的」穿透约定）。
"""

from __future__ import annotations

from pathlib import Path
from typing import Any

from ghrah.subject.config import SubjectConfig
from ghrah.subject.unit.base import CommandContext
from ghrah.subject.units.core_cluster import CoreClusterRegistryUnit


def _config(tmp_path: Path) -> SubjectConfig:
    return SubjectConfig(
        workspace_root=str(tmp_path / "workspace"),
        db_path=str(tmp_path / "subject.db"),
        manifest_root=str(tmp_path / "manifests"),
    )


class _FakePort:
    """端口替身：记录命令并按脚本回放。"""

    def __init__(self, reply: dict[str, Any] | None, error: Exception | None = None) -> None:
        self.reply = reply
        self.error = error
        self.calls: list[tuple[str, dict[str, Any]]] = []

    async def dispatch_command(
        self, command: str, payload: dict[str, Any]
    ) -> dict[str, Any] | None:
        self.calls.append((command, payload))
        if self.error is not None:
            raise self.error
        return self.reply


class _FakePorts:
    """CorePorts 替身：固定项目 → 端口映射。"""

    def __init__(self, ports: dict[str, _FakePort]) -> None:
        self._ports = ports

    def started_project_ids(self) -> list[str]:
        return list(self._ports)

    def for_project(self, project_id: str) -> Any:
        return self._ports[project_id]


class _FakeTransport:
    """PortClusterTransport 替身：只暴露扇出所需面。"""

    def __init__(self, ports: _FakePorts) -> None:
        self._ports = ports

    def started_project_ids(self) -> list[str]:
        return self._ports.started_project_ids()


def _unit_with_ports(tmp_path: Path, ports: dict[str, _FakePort]) -> CoreClusterRegistryUnit:
    unit = CoreClusterRegistryUnit(_config(tmp_path))
    fake = _FakePorts(ports)
    unit._transport = _FakeTransport(fake)  # type: ignore[assignment]
    unit._ports = fake  # type: ignore[assignment]
    return unit


def test_hitl_response_declared_in_routes(tmp_path: Path) -> None:
    """命令进路由表：``ctx.on("command/hitl_response")`` 由 mount_unit 注册。"""
    unit = CoreClusterRegistryUnit(_config(tmp_path))
    assert "hitl_response" in unit.meta.routes.commands


async def test_hitl_response_fanout_first_claimed_wins(tmp_path: Path) -> None:
    """穿透约定：首个项目不认领（None）→ 继续扇出，第二个项目认领胜出。"""
    passthrough = _FakePort(reply=None)
    claimed = _FakePort(reply={"success": True, "data": {"resolved": True}})
    unit = _unit_with_ports(tmp_path, {"p-first": passthrough, "p-second": claimed})

    result = await unit.handle_command(
        "hitl_response",
        {"promise_id": "prom-1", "approved": True},
        CommandContext.internal(),
    )

    assert result == {"success": True, "data": {"resolved": True}}
    # 两个项目都被尝试过（扇出顺序遍历，未认领者不阻断）。
    assert passthrough.calls == [("hitl_response", {"promise_id": "prom-1", "approved": True})]
    assert claimed.calls == [("hitl_response", {"promise_id": "prom-1", "approved": True})]


async def test_hitl_response_no_started_projects(tmp_path: Path) -> None:
    """零已启动项目：显式失败（不伪装成功），文案对齐 CoreUnit 既有语义。"""
    unit = _unit_with_ports(tmp_path, {})

    result = await unit.handle_command(
        "hitl_response",
        {"promise_id": "prom-1", "approved": True},
        CommandContext.internal(),
    )

    assert result["success"] is False
    assert "no active project runtime" in result["error"]


async def test_hitl_response_unclaimed_everywhere(tmp_path: Path) -> None:
    """全部项目穿透：回执明确「无人认领」（区别于路由不存在）。"""
    unit = _unit_with_ports(tmp_path, {"p-a": _FakePort(reply=None)})

    result = await unit.handle_command(
        "hitl_response",
        {"promise_id": "prom-x", "approved": True},
        CommandContext.internal(),
    )

    assert result["success"] is False
    assert "no unit claimed the response" in result["error"]


async def test_hitl_response_single_project_error_aggregated(tmp_path: Path) -> None:
    """单项目异常不阻断扇出：其余项目仍被尝试，失败原因聚合回报。"""
    broken = _FakePort(reply=None, error=RuntimeError("unit exploded"))
    healthy = _FakePort(reply={"success": True, "data": {"resolved": True}})
    unit = _unit_with_ports(tmp_path, {"p-broken": broken, "p-ok": healthy})

    result = await unit.handle_command(
        "hitl_response",
        {"promise_id": "prom-2", "approved": True},
        CommandContext.internal(),
    )

    # 健康项目认领成功——异常项目不吞掉整条命令。
    assert result["success"] is True

    # 全部异常时的聚合路径：
    unit2 = _unit_with_ports(tmp_path, {"p-only": broken})
    result2 = await unit2.handle_command(
        "hitl_response",
        {"promise_id": "prom-3", "approved": True},
        CommandContext.internal(),
    )
    assert result2["success"] is False
    assert "p-only" in str(result2["error"])
    assert "unit exploded" in str(result2["error"])


async def test_hitl_response_bad_payload_reports_unit_error(tmp_path: Path) -> None:
    """载荷缺定位键：单元的显式失败回执原样透传（不被扇出层吞掉）。"""
    validation_error = {
        "success": False,
        "error": "hitl_response requires either promise_id or the complete triplet",
    }
    unit = _unit_with_ports(tmp_path, {"p-a": _FakePort(reply=validation_error)})

    result = await unit.handle_command(
        "hitl_response",
        {"approved": True},
        CommandContext.internal(),
    )

    assert result["success"] is False
    assert "promise_id" in str(result["error"])
