// SPDX-FileCopyrightText: 2026 chenxya <chenxya@ghrah.org>
//
// SPDX-License-Identifier: Apache-2.0

import type { ActionNode } from "@ghrah/protocol";
import { defineStore } from "pinia";
import { ref } from "vue";
import { type FileChange, projectNodeToFileChanges } from "../projection.js";
import type { AgentTarget, ChainTarget } from "../scope.js";

export const useChangesStore = defineStore("ghrah-changes", () => {
  const changes = ref<FileChange[]>([]);
  const seenKeys = new Set<string>();

  function onActionChainNode(target: ChainTarget, node: ActionNode) {
    const projected = projectNodeToFileChanges(node, target);
    if (projected.length === 0) return;
    const next = [...changes.value];
    let appended = false;
    for (const change of projected) {
      const key = JSON.stringify([
        change.projectId,
        change.agentId,
        change.sessionId,
        change.branchId,
        change.nodeId,
        change.abilityName,
        change.filePath ?? "",
      ]);
      if (seenKeys.has(key)) continue;
      seenKeys.add(key);
      next.push(change);
      appended = true;
    }
    if (appended) changes.value = next;
  }

  function rebuildSeenKeys() {
    seenKeys.clear();
    for (const change of changes.value) {
      seenKeys.add(
        JSON.stringify([
          change.projectId,
          change.agentId,
          change.sessionId,
          change.branchId,
          change.nodeId,
          change.abilityName,
          change.filePath ?? "",
        ]),
      );
    }
  }

  function clearAgent(target: AgentTarget) {
    changes.value = changes.value.filter(
      (change) => change.projectId !== target.projectId || change.agentId !== target.agentId,
    );
    rebuildSeenKeys();
  }

  function clearProject(projectId: string) {
    changes.value = changes.value.filter((change) => change.projectId !== projectId);
    rebuildSeenKeys();
  }

  function clearAll() {
    changes.value = [];
    seenKeys.clear();
  }

  return { changes, onActionChainNode, clearAgent, clearProject, clearAll };
});
