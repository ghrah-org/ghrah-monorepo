// SPDX-FileCopyrightText: 2026 chenxya <chenxya@ghrah.org>
//
// SPDX-License-Identifier: Apache-2.0

import type { AgentTarget, RoomTarget } from "./scope.js";
import { useActionChainsStore } from "./stores/action-chains.js";
import { useAgentsStore } from "./stores/agents.js";
import { useBranchesStore } from "./stores/branches.js";
import { useChangesStore } from "./stores/changes.js";
import { useChatStore } from "./stores/chat.js";
import { useContextUsageStore } from "./stores/context-usage.js";
import { useHitlStore } from "./stores/hitl.js";
import { useRoomsStore } from "./stores/rooms.js";
import { useSessionsStore } from "./stores/sessions.js";
import { useTasksStore } from "./stores/tasks.js";

export function clearAgentDerived(target: AgentTarget) {
  useActionChainsStore().clearAgent(target);
  useBranchesStore().clearAgent(target);
  useSessionsStore().clearAgent(target);
  useChangesStore().clearAgent(target);
  useHitlStore().clearAgent(target);
  useChatStore().clearAgent(target);
  useContextUsageStore().clearAgent(target);
}

export function clearRoomDerived(target: RoomTarget, removeRoom = true) {
  useChatStore().clearRoom(target);
  const rooms = useRoomsStore();
  if (removeRoom) rooms.clearRoom(target);
  else rooms.clearRoomLog(target);
}

export function clearProjectDerived(projectId: string) {
  useActionChainsStore().clearProject(projectId);
  useBranchesStore().clearProject(projectId);
  useSessionsStore().clearProject(projectId);
  useChangesStore().clearProject(projectId);
  useHitlStore().clearProject(projectId);
  useChatStore().clearProject(projectId);
  useContextUsageStore().clearProject(projectId);
  useTasksStore().clearProject(projectId);
  useRoomsStore().clearProject(projectId);
  useAgentsStore().clearProject(projectId);
}
