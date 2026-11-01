# SPDX-FileCopyrightText: 2026 chenxya <chenxya@ghrah.org>
#
# SPDX-License-Identifier: Apache-2.0

"""Ability 类型分组（单一真源）。

runner（本地实例化）与 router（分布式实例化）共用这些集合，
避免 ``_fs_handler_types`` / ``_fs_ability_types`` 两份副本漂移。

从原能力门面迁入（那个包已随目录重构删除）：
集合本身属于能力分类事实，与门面无关。
"""

from __future__ import annotations

__all__ = ["FS_ABILITY_TYPES", "CLUSTER_ABILITY_TYPES"]

FS_ABILITY_TYPES: frozenset[str] = frozenset(
    {
        "read_file",
        "write_file",
        "list_directory",
        "edit_file",
        "move_file",
        "delete_file",
        "search_files",
    }
)
"""文件系统类 Ability 的 handler 类型集合（单一真源）。"""

CLUSTER_ABILITY_TYPES: frozenset[str] = frozenset(
    {
        "query_agents",
        "send_message",
        "broadcast_message",
        "spawn_agent",
        "terminate_agent",
        "query_manifests",
        "send",
    }
)
"""集群通信类 Ability 的 handler 类型集合（单一真源）。"""
