// SPDX-FileCopyrightText: 2026 chenxya <chenxya@ghrah.org>
//
// SPDX-License-Identifier: Apache-2.0

import type { ActionNode } from "@ghrah/protocol";
import type { AgentTarget, SessionTarget } from "./scope.js";
import type { useActionChainsStore } from "./stores/action-chains.js";
import type { useBranchesStore } from "./stores/branches.js";
import type { useSessionsStore } from "./stores/sessions.js";

type SessionsStore = ReturnType<typeof useSessionsStore>;
type BranchesStore = ReturnType<typeof useBranchesStore>;
type ChainsStore = ReturnType<typeof useActionChainsStore>;

export interface VisibleNodesOptions {
  /** 查看的 Session ID；缺省回退 viewed/runtime Session。 */
  sessionId?: string | null;
  /** 查看的 Branch ID；null = 查看 Session 全部 Branch；undefined = 回退 viewed。 */
  branchId?: string | null;
}

/**
 * 计算可视节点集：先按 viewedSessionId 隔离 Root，再从 Branch Head(s) 回溯祖先。
 *
 * - 单 Branch 模式：从该 Branch 的 head_node_id 回溯。
 * - 全部 Branch 模式：合并本 Session 所有非 deleted Branch Head 的祖先集。
 * - 缺失父节点：保留已知部分并停止该路径。
 * - 跨 Session parent 或环：拒绝该边。
 * - 排序：iteration → timestamp → id 稳定排序并按 id 去重。
 */
export function getVisibleNodes(
  sessions: SessionsStore,
  branches: BranchesStore,
  chains: ChainsStore,
  agent: AgentTarget,
  options: VisibleNodesOptions = {},
): ActionNode[] {
  const sessionId =
    options.sessionId !== undefined && options.sessionId !== null
      ? options.sessionId
      : sessions.viewedSessionId(agent);
  if (!sessionId) return [];
  const sessionTarget: SessionTarget = { ...agent, sessionId };

  let branchId = options.branchId;
  if (branchId === undefined) {
    const viewed = branches.viewedBranchId(sessionTarget);
    branchId = viewed === undefined ? branches.activeBranchId(sessionTarget) : viewed;
  }

  const sessionBranches = branches.branchesForSession(sessionTarget);
  const selected =
    branchId === null
      ? sessionBranches.filter((item) => item.info.lifecycle !== "deleted")
      : sessionBranches.filter((item) => item.target.branchId === branchId);
  if (selected.length === 0) return [];

  // 节点索引：本 Session 的全部已知节点（跨 Branch 桶收集）。
  const nodesById = new Map<string, ActionNode>();
  for (const node of chains.nodesForSession(sessionTarget)) {
    if (node.id) nodesById.set(node.id, node);
  }
  if (nodesById.size === 0) return [];

  const visited = new Set<string>();
  const result: ActionNode[] = [];

  function walk(nodeId: string | null | undefined) {
    while (nodeId) {
      if (visited.has(nodeId)) return;
      const node = nodesById.get(nodeId);
      if (!node) return; // 缺失父节点：保留已知部分并停止该路径
      visited.add(nodeId);
      result.push(node);
      const parentId: string | null | undefined = node.parent_id;
      if (parentId) {
        const parent = nodesById.get(parentId);
        // 跨 Session parent 或环：拒绝该边（parent 属于其他 Session 时停止）。
        if (!parent || parent.session_id !== sessionId) return;
      }
      nodeId = parentId;
    }
  }

  for (const branch of selected) {
    walk(branch.info.head_node_id);
  }

  result.sort(
    (a, b) =>
      a.iteration - b.iteration ||
      (a.timestamp ?? "").localeCompare(b.timestamp ?? "") ||
      (a.id ?? "").localeCompare(b.id ?? ""),
  );
  return result;
}
