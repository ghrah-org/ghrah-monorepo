// SPDX-FileCopyrightText: 2026 chenxya <chenxya@ghrah.org>
//
// SPDX-License-Identifier: Apache-2.0

import type { HITLRequestPayload } from "@ghrah/protocol";
import { defineStore } from "pinia";
import { computed, ref } from "vue";
import type { AgentTarget } from "../scope.js";

export interface HitlRequest {
  promiseId: string;
  projectId: string;
  agentId: string;
  agentName: string;
  abilityName: string;
  toolArgs: Record<string, unknown>;
  context: Record<string, unknown>;
  reason: string;
}

export const useHitlStore = defineStore("ghrah-hitl", () => {
  const requests = ref<HitlRequest[]>([]);
  const pendingRequests = computed(() => requests.value);
  const selectedIds = ref<Set<string>>(new Set());

  function onHitlRequest(payload: HITLRequestPayload): boolean {
    if (!payload.project_id || !payload.agent_id) return false;
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
    });
    return true;
  }

  function removeRequest(promiseId: string) {
    requests.value = requests.value.filter((request) => request.promiseId !== promiseId);
    selectedIds.value.delete(promiseId);
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
    requests.value = requests.value.filter(
      (request) => request.projectId !== target.projectId || request.agentId !== target.agentId,
    );
  }

  function clearProject(projectId: string) {
    requests.value = requests.value.filter((request) => request.projectId !== projectId);
  }

  function clearAll() {
    requests.value = [];
    selectedIds.value = new Set();
  }

  return {
    requests,
    pendingRequests,
    selectedIds,
    onHitlRequest,
    removeRequest,
    toggleSelected,
    selectAll,
    clearSelection,
    clearAgent,
    clearProject,
    clearAll,
  };
});
