// SPDX-FileCopyrightText: 2026 chenxya <chenxya@ghrah.org>
//
// SPDX-License-Identifier: Apache-2.0

import type { ActionChainUpdatedPayload, ActionNode } from "@ghrah/protocol";
import { defineStore } from "pinia";
import { ref } from "vue";
import {
  type ChainTarget,
  chainKey,
  hasCompleteChainTarget,
  type SessionTarget,
} from "../scope.js";

export interface ActionChainProjection {
  target: ChainTarget;
  nodes: ActionNode[];
}

export interface ProjectionDiagnostic {
  reason: "incomplete_chain_target";
  eventType: "action_chain_updated";
  projectId: string;
  agentId: string;
  agentName: string;
  sessionId: string;
  branchId: string;
  nodeId: string;
}

const MAX_DIAGNOSTICS = 50;

export const useActionChainsStore = defineStore("ghrah-action-chains", () => {
  const chains = ref<Map<string, ActionChainProjection>>(new Map());
  const diagnostics = ref<ProjectionDiagnostic[]>([]);

  function reject(payload: ActionChainUpdatedPayload, node: ActionNode) {
    diagnostics.value = [
      ...diagnostics.value.slice(-(MAX_DIAGNOSTICS - 1)),
      {
        reason: "incomplete_chain_target",
        eventType: "action_chain_updated",
        projectId: payload.project_id,
        agentId: payload.agent_id,
        agentName: payload.agent_name,
        sessionId: node.session_id,
        branchId: node.created_on_branch_id,
        nodeId: node.id,
      },
    ];
  }

  function mergeNodes(target: ChainTarget, nodes: ActionNode[]) {
    const existing = chains.value.get(chainKey(target))?.nodes ?? [];
    const seen = new Set(existing.map((node) => node.id).filter(Boolean));
    const merged = [...existing];
    for (const node of nodes) {
      if (node.id && seen.has(node.id)) continue;
      if (node.id) seen.add(node.id);
      merged.push(node);
    }
    chains.value.set(chainKey(target), { target, nodes: merged });
  }

  function onActionChainUpdated(payload: ActionChainUpdatedPayload): boolean {
    const node = payload.node;
    if (!node) return false;
    const target: ChainTarget = {
      projectId: payload.project_id,
      agentId: payload.agent_id,
      agentName: payload.agent_name,
      sessionId: node.session_id,
      branchId: node.created_on_branch_id,
    };
    if (!hasCompleteChainTarget(target)) {
      reject(payload, node);
      return false;
    }
    mergeNodes(target, [node]);
    return true;
  }

  function setChain(target: ChainTarget, nodes: ActionNode[]) {
    if (!hasCompleteChainTarget(target)) return;
    const seen = new Set<string>();
    const snapshot = nodes.filter((node) => {
      if (!node.id) return true;
      if (seen.has(node.id)) return false;
      seen.add(node.id);
      return true;
    });
    chains.value.set(chainKey(target), { target, nodes: snapshot });
  }

  /** history 结果只合并不可变 node，不整体覆盖既有桶（抗乱序增量补齐）。 */
  function mergeHistory(target: ChainTarget, nodes: ActionNode[]) {
    if (!hasCompleteChainTarget(target)) return;
    mergeNodes(target, nodes);
  }

  function getChain(target: ChainTarget): ActionNode[] {
    return chains.value.get(chainKey(target))?.nodes ?? [];
  }

  function nodesForSession(target: SessionTarget): ActionNode[] {
    const collected: ActionNode[] = [];
    for (const chain of chains.value.values()) {
      if (
        chain.target.projectId === target.projectId &&
        chain.target.agentId === target.agentId &&
        chain.target.sessionId === target.sessionId
      ) {
        collected.push(...chain.nodes);
      }
    }
    return collected;
  }

  function clearChain(target: ChainTarget) {
    chains.value.delete(chainKey(target));
  }

  function clearSession(target: SessionTarget) {
    chains.value = new Map(
      [...chains.value.entries()].filter(
        ([, chain]) =>
          chain.target.projectId !== target.projectId ||
          chain.target.agentId !== target.agentId ||
          chain.target.sessionId !== target.sessionId,
      ),
    );
  }

  function clearAgent(target: Pick<ChainTarget, "projectId" | "agentId">) {
    chains.value = new Map(
      [...chains.value.entries()].filter(
        ([, chain]) =>
          chain.target.projectId !== target.projectId || chain.target.agentId !== target.agentId,
      ),
    );
  }

  function clearProject(projectId: string) {
    chains.value = new Map(
      [...chains.value.entries()].filter(([, chain]) => chain.target.projectId !== projectId),
    );
  }

  function clearAll() {
    chains.value = new Map();
    diagnostics.value = [];
  }

  return {
    chains,
    diagnostics,
    onActionChainUpdated,
    setChain,
    mergeHistory,
    getChain,
    nodesForSession,
    clearChain,
    clearSession,
    clearAgent,
    clearProject,
    clearAll,
  };
});
