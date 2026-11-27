// SPDX-FileCopyrightText: 2026 chenxya <chenxya@ghrah.org>
//
// SPDX-License-Identifier: Apache-2.0

import type { HitlRequestListView, HITLRequestPayload } from "@ghrah/protocol";
import { defineStore } from "pinia";
import { computed, ref } from "vue";
import { agentKey, type AgentTarget, sameAgent } from "../scope.js";

export interface HitlRequest {
  promiseId: string;
  projectId: string;
  agentId: string;
  agentName: string;
  abilityName: string;
  toolArgs: Record<string, unknown>;
  context: Record<string, unknown>;
  reason: string;
  turnId?: string | null;
  deadline?: string | null;
  shellRemember?: HITLRequestPayload["shell_remember"];
}

export const useHitlStore = defineStore("ghrah-hitl", () => {
  const requests = ref<HitlRequest[]>([]);
  const pendingRequests = computed(() => requests.value);
  const selectedIds = ref<Set<string>>(new Set());
  const pendingReads = new Map<string, { target: AgentTarget; changed: Set<string> }>();

  function onHitlRequest(payload: HITLRequestPayload): boolean {
    if (!payload.project_id || !payload.agent_id) return false;
    pendingReads
      .get(agentKey({ projectId: payload.project_id, agentId: payload.agent_id }))
      ?.changed.add(payload.promise_id);
    if (requests.value.some((request) => request.promiseId === payload.promise_id)) return true;
    requests.value.push({
      promiseId: payload.promise_id,
      projectId: payload.project_id,
      agentId: payload.agent_id,
      agentName: payload.agent_name,
      abilityName: payload.ability_name,
      toolArgs: payload.tool_args,
      context: payload.context,
      reason: payload.reason ?? "",
      ...(payload.turn_id ? { turnId: payload.turn_id } : {}),
      ...(payload.deadline_at ? { deadline: payload.deadline_at } : {}),
      shellRemember: payload.shell_remember ?? null,
    });
    return true;
  }

  function removeRequest(promiseId: string) {
    for (const read of pendingReads.values()) read.changed.add(promiseId);
    requests.value = requests.value.filter((request) => request.promiseId !== promiseId);
    selectedIds.value.delete(promiseId);
  }

  function replaceRequests(next: HitlRequest[]) {
    requests.value = next;
    const remaining = new Set(next.map(({ promiseId }) => promiseId));
    selectedIds.value = new Set([...selectedIds.value].filter((id) => remaining.has(id)));
  }

  /** 冻结读取范围，并保留请求发出后收到的实时变化。 */
  function beginPendingRead(target: AgentTarget) {
    const key = agentKey(target);
    const read = { target, changed: new Set<string>() };
    pendingReads.set(key, read);
    return (items?: HitlRequestListView["items"]): boolean => {
      if (pendingReads.get(key) !== read) return false;
      pendingReads.delete(key);
      if (items === undefined) return true;

      const snapshotIds = new Set(items.map(({ promise_id }) => promise_id));
      const next = new Map(requests.value.map((request) => [request.promiseId, request]));
      for (const request of requests.value) {
        if (
          sameAgent(request, target) &&
          !snapshotIds.has(request.promiseId) &&
          !read.changed.has(request.promiseId)
        ) {
          next.delete(request.promiseId);
        }
      }
      for (const item of items) {
        if (read.changed.has(item.promise_id)) continue;
        next.set(item.promise_id, {
          ...target,
          promiseId: item.promise_id,
          abilityName: item.ability_name,
          toolArgs: item.tool_call,
          context: next.get(item.promise_id)?.context ?? {},
          reason: item.reason,
          turnId: item.turn_id,
          ...(item.deadline_at ? { deadline: item.deadline_at } : {}),
          shellRemember: item.shell_remember ?? null,
        });
      }
      replaceRequests([...next.values()]);
      return true;
    };
  }

  function cancelPendingReads() {
    pendingReads.clear();
  }

  function toggleSelected(promiseId: string) {
    const next = new Set(selectedIds.value);
    if (next.has(promiseId)) {
      next.delete(promiseId);
    } else {
      next.add(promiseId);
    }
    selectedIds.value = next;
  }

  function selectAll() {
    selectedIds.value = new Set(requests.value.map((request) => request.promiseId));
  }

  function clearSelection() {
    selectedIds.value = new Set();
  }

  function clearAgent(target: AgentTarget) {
    pendingReads.delete(agentKey(target));
    replaceRequests(requests.value.filter((request) => !sameAgent(request, target)));
  }

  function clearProject(projectId: string) {
    for (const [key, read] of pendingReads) {
      if (read.target.projectId === projectId) pendingReads.delete(key);
    }
    replaceRequests(requests.value.filter((request) => request.projectId !== projectId));
  }

  function clearAll() {
    cancelPendingReads();
    replaceRequests([]);
  }

  return {
    requests,
    pendingRequests,
    selectedIds,
    onHitlRequest,
    removeRequest,
    beginPendingRead,
    cancelPendingReads,
    toggleSelected,
    selectAll,
    clearSelection,
    clearAgent,
    clearProject,
    clearAll,
  };
});
