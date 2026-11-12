// SPDX-FileCopyrightText: 2026 chenxya <chenxya@ghrah.org>
//
// SPDX-License-Identifier: Apache-2.0

import type {
  AgentConfigPayload,
  AgentSpawnedPayload,
  AgentTerminatedPayload,
  ProjectInfoPayload,
} from "@ghrah/protocol";
import { defineStore } from "pinia";
import { computed, ref } from "vue";
import {
  type AgentKey,
  type AgentTarget,
  agentKey,
  hasCompleteAgentKey,
  sameAgent,
} from "../scope.js";

export interface AgentListItem {
  project_id?: string;
  agent_id?: string;
  name: string;
  runtime_state?: string;
  runtime_status?: string;
  runtime_error?: string;
  incarnation_id?: string;
  recovery_mode?: string;
  config?: AgentConfigPayload;
}

export interface AgentInfo extends AgentTarget {
  incarnationId: string;
  recoveryMode: string;
  runtimeStatus: string;
  runtimeError: string;
  config?: AgentConfigPayload;
  /** @deprecated Use runtimeStatus. Kept until the M4 shell migration. */
  status: "active" | "terminated";
}

function projectIsOperable(project?: Pick<ProjectInfoPayload, "status" | "archived_at">): boolean {
  return project?.status === "active" && project.archived_at == null;
}

function normalizeRuntimeStatus(
  item: AgentListItem,
  project?: Pick<ProjectInfoPayload, "status" | "archived_at">,
): string {
  if (!projectIsOperable(project)) return "stopped";
  return item.runtime_state || item.runtime_status || "stopped";
}

function compatibilityStatus(runtimeStatus: string): AgentInfo["status"] {
  return runtimeStatus === "running" || runtimeStatus === "active" ? "active" : "terminated";
}

export const useAgentsStore = defineStore("ghrah-agents", () => {
  const agents = ref<Map<string, AgentInfo>>(new Map());
  const selectedAgentTarget = ref<AgentTarget | null>(null);

  const activeAgents = computed(() =>
    Array.from(agents.value.values()).filter((agent) => agent.status === "active"),
  );
  const selectedAgentName = computed(() => selectedAgentTarget.value?.agentName ?? null);
  const selectedAgent = computed(() => {
    if (!selectedAgentTarget.value) return null;
    return agents.value.get(agentKey(selectedAgentTarget.value)) ?? null;
  });

  function agentsForProject(projectId: string): AgentInfo[] {
    return [...agents.value.values()].filter((agent) => agent.projectId === projectId);
  }

  function activeAgentsForProject(projectId: string): AgentInfo[] {
    return agentsForProject(projectId).filter((agent) => agent.status === "active");
  }

  function getAgent(target: AgentKey): AgentInfo | undefined {
    return agents.value.get(agentKey(target));
  }

  function selectAgent(target: AgentTarget | null) {
    selectedAgentTarget.value = target;
  }

  function onAgentSpawned(
    payload: AgentSpawnedPayload,
    project?: Pick<ProjectInfoPayload, "status" | "archived_at">,
  ) {
    const target: AgentTarget = {
      projectId: payload.project_id,
      agentId: payload.agent_id || payload.config.agent_id || "",
      agentName: payload.name,
    };
    if (!hasCompleteAgentKey(target)) return;
    const runtimeStatus = project && !projectIsOperable(project) ? "stopped" : "running";
    agents.value.set(agentKey(target), {
      ...target,
      incarnationId: payload.incarnation_id,
      recoveryMode: payload.recovery_mode,
      runtimeStatus,
      runtimeError: "",
      config: payload.config,
      status: compatibilityStatus(runtimeStatus),
    });
  }

  function onAgentTerminated(payload: AgentTerminatedPayload): AgentTarget | null {
    const target: AgentTarget = {
      projectId: payload.project_id,
      agentId: payload.agent_id,
      agentName: payload.name,
    };
    if (!hasCompleteAgentKey(target)) return null;
    const existing = agents.value.get(agentKey(target));
    if (existing) {
      existing.runtimeStatus = "stopped";
      existing.status = "terminated";
    }
    if (sameAgent(selectedAgentTarget.value, target)) selectedAgentTarget.value = null;
    return target;
  }

  function replaceProjectAgents(
    projectId: string,
    list: AgentListItem[],
    project?: Pick<ProjectInfoPayload, "status" | "archived_at">,
  ): AgentTarget[] {
    const previous = agentsForProject(projectId);
    const next = new Map(
      [...agents.value.entries()].filter(([, agent]) => agent.projectId !== projectId),
    );
    const retained = new Set<string>();

    for (const item of list) {
      const target: AgentTarget = {
        projectId: item.project_id || projectId,
        agentId: item.agent_id || item.config?.agent_id || "",
        agentName: item.name,
      };
      if (target.projectId !== projectId || !hasCompleteAgentKey(target)) continue;
      const runtimeStatus = normalizeRuntimeStatus(item, project);
      const key = agentKey(target);
      retained.add(key);
      next.set(key, {
        ...target,
          incarnationId: item.incarnation_id || "",
        recoveryMode: item.recovery_mode || "",
        runtimeStatus,
        runtimeError: item.runtime_error || "",
        config: item.config,
        status: compatibilityStatus(runtimeStatus),
      });
    }
    agents.value = next;

    const removed = previous
      .filter((agent) => !retained.has(agentKey(agent)))
      .map(({ projectId, agentId, agentName }) => ({ projectId, agentId, agentName }));
    if (
      selectedAgentTarget.value?.projectId === projectId &&
      !retained.has(agentKey(selectedAgentTarget.value))
    ) {
      selectedAgentTarget.value = null;
    }
    return removed;
  }

  function removeAgent(target: AgentTarget) {
    agents.value.delete(agentKey(target));
    if (sameAgent(selectedAgentTarget.value, target)) selectedAgentTarget.value = null;
  }

  function clearProject(projectId: string) {
    agents.value = new Map(
      [...agents.value.entries()].filter(([, agent]) => agent.projectId !== projectId),
    );
    if (selectedAgentTarget.value?.projectId === projectId) selectedAgentTarget.value = null;
  }

  function applyProjectRuntimeState(
    project: Pick<ProjectInfoPayload, "project_id" | "status" | "archived_at">,
  ) {
    if (project.status === "active" && project.archived_at == null) return;
    const next = new Map(agents.value);
    for (const [key, agent] of next) {
      if (agent.projectId !== project.project_id) continue;
      next.set(key, { ...agent, runtimeStatus: "stopped", status: "terminated" });
    }
    agents.value = next;
  }

  function clearAll() {
    agents.value = new Map();
    selectedAgentTarget.value = null;
  }

  return {
    agents,
    selectedAgentTarget,
    selectedAgentName,
    activeAgents,
    selectedAgent,
    agentsForProject,
    activeAgentsForProject,
    getAgent,
    selectAgent,
    onAgentSpawned,
    onAgentTerminated,
    replaceProjectAgents,
    removeAgent,
    clearProject,
    applyProjectRuntimeState,
    clearAll,
  };
});
