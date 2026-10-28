# SPDX-FileCopyrightText: 2026 chenxya <chenxya@ghrah.org>
#
# SPDX-License-Identifier: Apache-2.0
"""命令路由分组与事件归属契约。

供 Subject 路由层判断命令归属（Core / Subject 本地 / 各域处理器）与
Agent 作用域事件必须携带的归属字段。
"""

from __future__ import annotations

from ghrah.protocol.enums import CommandType, EventType

# 必须携带非空 project_id + agent_id 的事件类型（Agent 作用域事件归属契约）。
# 生产侧缺失归属时跳过发布，转发侧（Subject EventBus）缺失归属时丢弃并计数。


AGENT_SCOPED_EVENT_TYPES: frozenset[str] = frozenset(
    {
        EventType.AGENT_SPAWNED.value,
        EventType.AGENT_TERMINATED.value,
        EventType.AGENT_RESPONSE.value,
        EventType.AGENT_ERROR.value,
        EventType.ACTION_CHAIN_UPDATED.value,
        EventType.ABILITY_RESULT.value,
        EventType.HITL_REQUEST.value,
        EventType.HITL_RESOLVED.value,
        EventType.CONTEXT_USAGE_UPDATED.value,
        EventType.SESSION_CREATED.value,
        EventType.SESSION_ACTIVATED.value,
        EventType.SESSION_ARCHIVED.value,
        EventType.SESSION_DELETED.value,
        EventType.SESSION_LIST_RESULT.value,
        EventType.BRANCH_CREATED.value,
        EventType.BRANCH_ACTIVATED.value,
        EventType.BRANCH_ARCHIVED.value,
        EventType.BRANCH_DELETED.value,
        EventType.BRANCH_LIST_RESULT.value,
    }
)

PERSIST_COMMANDS: frozenset[str] = frozenset(
    {
        CommandType.PERSIST_SAVE_NODE.value,
        CommandType.PERSIST_LOAD_NODE.value,
        CommandType.PERSIST_LOAD_CHAIN.value,
        CommandType.PERSIST_SAVE_MESSAGES.value,
        CommandType.PERSIST_LOAD_MESSAGES.value,
        CommandType.PERSIST_DELETE_CHAIN.value,
        CommandType.PERSIST_LIST_AGENTS.value,
    }
)


CORE_COMMANDS: frozenset[str] = frozenset(
    {
        CommandType.SPAWN_AGENT.value,
        CommandType.TERMINATE_AGENT.value,
        CommandType.SEND_MESSAGE.value,
        CommandType.BROADCAST_MESSAGE.value,
        CommandType.REGISTER_ABILITY.value,
        CommandType.UNREGISTER_ABILITY.value,
        CommandType.LIST_AGENTS.value,
        CommandType.HEALTH_CHECK.value,
        CommandType.DELEGATE.value,
        CommandType.GET_AGENT_INFO.value,
        CommandType.AGENT_COMPACT_CONTEXT.value,
        CommandType.AGENT_RESET.value,
        CommandType.INIT_CLUSTER.value,
        CommandType.SHUTDOWN_CLUSTER.value,
        CommandType.CLUSTER_STATUS.value,
        CommandType.LIST_CLUSTERS.value,
        CommandType.SESSION_CREATE.value,
        CommandType.SESSION_ACTIVATE.value,
        CommandType.SESSION_LIST.value,
        CommandType.SESSION_ARCHIVE.value,
        CommandType.SESSION_DELETE.value,
        CommandType.BRANCH_CREATE.value,
        CommandType.BRANCH_ACTIVATE.value,
        CommandType.BRANCH_LIST.value,
        CommandType.BRANCH_ARCHIVE.value,
        CommandType.BRANCH_DELETE.value,
    }
)


SESSION_COMMANDS: frozenset[str] = frozenset(
    {
        CommandType.SESSION_CREATE.value,
        CommandType.SESSION_ACTIVATE.value,
        CommandType.SESSION_LIST.value,
        CommandType.SESSION_ARCHIVE.value,
        CommandType.SESSION_DELETE.value,
    }
)


BRANCH_COMMANDS: frozenset[str] = frozenset(
    {
        CommandType.BRANCH_CREATE.value,
        CommandType.BRANCH_ACTIVATE.value,
        CommandType.BRANCH_LIST.value,
        CommandType.BRANCH_ARCHIVE.value,
        CommandType.BRANCH_DELETE.value,
    }
)


WORKSPACE_COMMANDS: frozenset[str] = frozenset(
    {
        CommandType.CREATE_WORKSPACE.value,
        CommandType.DESTROY_WORKSPACE.value,
        CommandType.WORKSPACE_SNAPSHOT.value,
        CommandType.WORKSPACE_ROLLBACK.value,
        CommandType.WORKSPACE_DIFF.value,
        CommandType.WORKSPACE_STATUS.value,
        CommandType.WORKSPACE_REGISTER.value,
        CommandType.WORKSPACE_GET.value,
        CommandType.WORKSPACE_LIST.value,
    }
)


MANIFEST_COMMANDS: frozenset[str] = frozenset(
    {
        CommandType.MANIFEST_LIST_ABILITIES.value,
        CommandType.MANIFEST_GET_ABILITY.value,
        CommandType.MANIFEST_PUT_ABILITY.value,
        CommandType.MANIFEST_DELETE_ABILITY.value,
        CommandType.MANIFEST_LIST_AGENTS.value,
        CommandType.MANIFEST_GET_AGENT.value,
        CommandType.MANIFEST_PUT_AGENT.value,
        CommandType.MANIFEST_DELETE_AGENT.value,
        CommandType.MANIFEST_RESOLVE_AGENT.value,
        CommandType.MANIFEST_VALIDATE.value,
    }
)


CHAIN_HISTORY_COMMANDS: frozenset[str] = frozenset(
    {
        CommandType.GET_CHAIN_HISTORY.value,
    }
)


TASK_COMMANDS: frozenset[str] = frozenset(
    {
        CommandType.TASK_CREATE.value,
        CommandType.TASK_UPDATE.value,
        CommandType.TASK_ASSIGN.value,
        CommandType.TASK_START.value,
        CommandType.TASK_COMPLETE.value,
        CommandType.TASK_FAIL.value,
        CommandType.TASK_CANCEL.value,
        CommandType.TASK_BLOCK.value,
        CommandType.TASK_LIST.value,
        CommandType.TASK_GET.value,
        CommandType.TASK_DELETE.value,
    }
)


TASK_ATTRIBUTION_COMMANDS: frozenset[str] = frozenset(
    {
        CommandType.TASK_SUBMIT_COMPLETION.value,
        CommandType.TASK_VERIFY.value,
        CommandType.TASK_LIST_CLAIMS.value,
        CommandType.TASK_DUMP.value,
    }
)


PLUGIN_COMMANDS: frozenset[str] = frozenset(
    {
        CommandType.PLUGIN_NEGOTIATE.value,
    }
)


PROJECT_COMMANDS: frozenset[str] = frozenset(
    {
        CommandType.PROJECT_CREATE.value,
        CommandType.PROJECT_UPDATE.value,
        CommandType.PROJECT_LIST.value,
        CommandType.PROJECT_GET.value,
        CommandType.PROJECT_ARCHIVE.value,
        CommandType.PROJECT_RESTORE.value,
        CommandType.PROJECT_DELETE.value,
        CommandType.PROJECT_ADD_AGENT.value,
        CommandType.AGENT_ENTITY.value,
        CommandType.AGENT_ENTITY_CREATE.value,
        CommandType.AGENT_ENTITY_LIST.value,
        CommandType.PROJECT_REMOVE_AGENT.value,
        CommandType.PROJECT_LINK_TASK.value,
        CommandType.PROJECT_UNLINK_TASK.value,
        CommandType.PROJECT_SET_RECOVERY.value,
        CommandType.PROJECT_PAUSE.value,
        CommandType.PROJECT_RESUME.value,
        CommandType.PROJECT_STOP.value,
    }
)


ROOM_COMMANDS: frozenset[str] = frozenset(
    {
        CommandType.ROOM_CREATE.value,
        CommandType.ROOM_LIST.value,
        CommandType.ROOM_GET.value,
        CommandType.ROOM_UPDATE.value,
        CommandType.ROOM_ARCHIVE.value,
        CommandType.ROOM_RESTORE.value,
        CommandType.ROOM_DELETE.value,
        CommandType.ROOM_JOIN.value,
        CommandType.ROOM_LEAVE.value,
        CommandType.ROOM_GET_MEMBERS.value,
        CommandType.ROOM_GET_LOG.value,
        CommandType.ROOM_SEND.value,
    }
)


RECONCILE_COMMANDS: frozenset[str] = frozenset(
    {
        CommandType.RECONCILE_NOW.value,
        CommandType.RECONCILE_STATUS.value,
    }
)
