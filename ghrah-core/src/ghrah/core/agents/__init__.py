# SPDX-FileCopyrightText: 2026 chenxya <chenxya@ghrah.org>
#
# SPDX-License-Identifier: Apache-2.0
"""Agent 实例相关的 Core 侧实现：配置快照（权威）与装配输入解析。

两个模块的分工：

- :mod:`ghrah.core.agents.instance_store`：把「创建时的实例配置」落到
  ``<project_root>/db/agents.sqlite3``。它是运行期配置的**唯一基准**，重启按
  ``agent_id`` 读它恢复，不再回读 Subject 的模板。
- :mod:`ghrah.core.agents.assembly`：把端口的 ``AgentAssemblySpec`` 解析成
  spawn 需要的 ``ResolvedAgent``（配置 + 逐成员 ``ResolvedAbility``）。
  这是 v3 装配输入替换「读 YAML 模板再解析」的落点。
"""

from __future__ import annotations

from ghrah.core.agents.assembly import (
    AssemblyError,
    config_from_snapshot,
    config_to_snapshot,
    member_axes_from_spec,
    resolve_assembly,
    resolve_members,
    surface_from_snapshot,
)
from ghrah.core.agents.instance_store import (
    AgentInstanceRecord,
    AgentInstanceStore,
    AgentInstanceStoreError,
)

__all__ = [
    "AgentInstanceRecord",
    "AgentInstanceStore",
    "AgentInstanceStoreError",
    "AssemblyError",
    "config_from_snapshot",
    "config_to_snapshot",
    "member_axes_from_spec",
    "resolve_assembly",
    "resolve_members",
    "surface_from_snapshot",
]
