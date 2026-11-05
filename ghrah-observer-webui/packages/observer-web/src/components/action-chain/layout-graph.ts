// SPDX-FileCopyrightText: 2026 chenxya <chenxya@ghrah.org>
//
// SPDX-License-Identifier: Apache-2.0

import type { ActionNode, BranchInfoPayload } from "@ghrah/protocol";

export interface LayoutNode {
  node: ActionNode;
  /** 节点左上角 x（= 层号 × layerGap）。 */
  x: number;
  /** 节点垂直中心 y（= 泳道内槽位中心）。 */
  y: number;
  /** 所属泳道序号。 */
  laneIndex: number;
  /** 分叉点（某 branch 的 fork_point）。 */
  isBranchPoint: boolean;
  /** 某 branch 的 head。 */
  isHead: boolean;
}

export interface LayoutEdge {
  /** 父节点 id；根节点入边省略（不出现在 edges 中）。 */
  fromId: string | null;
  toId: string;
  /** SVG path d（三次贝塞尔）。 */
  path: string;
  /** 是否跨泳道边（分叉）。 */
  laneChange: boolean;
}

export interface LayoutGraph {
  nodes: LayoutNode[];
  edges: LayoutEdge[];
  width: number;
  height: number;
}

export interface LayoutGraphOptions {
  nodeWidth?: number;
  laneHeight?: number;
  layerGap?: number;
}

export const DEFAULT_NODE_WIDTH = 200;
export const DEFAULT_LANE_HEIGHT = 96;
export const DEFAULT_LAYER_GAP = 240;

export function nodeKey(a: ActionNode, b: ActionNode): number {
  return (
    (a.timestamp ?? "").localeCompare(b.timestamp ?? "") || (a.id ?? "").localeCompare(b.id ?? "")
  );
}

function branchKey(a: BranchInfoPayload, b: BranchInfoPayload): number {
  return (
    (a.created_at ?? "").localeCompare(b.created_at ?? "") || a.branch_id.localeCompare(b.branch_id)
  );
}

/** branch 泳道序：按 fork 依赖拓扑排序（父先于子），同级按创建先后；拓扑序号即泳道号。 */
export function branchLaneIndices(branches: BranchInfoPayload[]): Map<string, number> {
  const byId = new Map<string, BranchInfoPayload>();
  for (const branch of branches) {
    if (branch.branch_id && !byId.has(branch.branch_id)) byId.set(branch.branch_id, branch);
  }

  const indegree = new Map<string, number>();
  const childrenOf = new Map<string, string[]>();
  for (const branch of byId.values()) indegree.set(branch.branch_id, 0);
  for (const branch of byId.values()) {
    const pid = branch.parent_branch_id;
    if (!pid || !byId.has(pid) || pid === branch.branch_id) continue;
    indegree.set(branch.branch_id, (indegree.get(branch.branch_id) ?? 0) + 1);
    const arr = childrenOf.get(pid) ?? [];
    arr.push(branch.branch_id);
    childrenOf.set(pid, arr);
  }

  const laneOf = new Map<string, number>();
  const pending = [...byId.values()].sort(branchKey);
  let lane = 0;
  while (pending.length > 0) {
    // 取创建序最早的就绪 branch；环残余（无就绪项）强制取首个，保证不抛错
    let pick = pending.findIndex((b) => (indegree.get(b.branch_id) ?? 0) === 0);
    if (pick < 0) pick = 0;
    const [chosen] = pending.splice(pick, 1);
    laneOf.set(chosen.branch_id, lane);
    lane += 1;
    for (const child of childrenOf.get(chosen.branch_id) ?? []) {
      indegree.set(child, (indegree.get(child) ?? 1) - 1);
    }
  }
  return laneOf;
}

function round2(value: number): number {
  return Math.round(value * 100) / 100;
}

/**
 * 画布布局纯函数：输入 getVisibleNodes 输出（已去重、稳定排序）与同 Session 可视 branch，
 * 输出横向 Git 流式布局（层 → x，泳道 → y，贝塞尔边路径）。
 *
 * - 分层：layer = 距 root 的最长路径长度；缺失父节点/环回退为根（层 0），不抛错。
 * - 泳道：branch 按拓扑序各占一泳道（main 先创建即泳道 0）；节点按 created_on_branch_id
 *   归属，无 branch 元数据回退泳道 0（branch 缺失时退化为单泳道布局）。
 * - 同（泳道, 层）多节点（多 Root/孤儿等退化输入）按 (timestamp, id) 分槽避免重叠。
 * - 边：父右缘 → 子左缘三次贝塞尔；根与缺失父的入边省略。
 */
export function layoutGraph(
  nodes: ActionNode[],
  branches: BranchInfoPayload[],
  options: LayoutGraphOptions = {},
): LayoutGraph {
  const nodeWidth = options.nodeWidth ?? DEFAULT_NODE_WIDTH;
  const laneHeight = options.laneHeight ?? DEFAULT_LANE_HEIGHT;
  const layerGap = options.layerGap ?? DEFAULT_LAYER_GAP;

  if (nodes.length === 0) return { nodes: [], edges: [], width: 0, height: 0 };

  const nodesById = new Map<string, ActionNode>();
  for (const n of nodes) {
    if (n.id && !nodesById.has(n.id)) nodesById.set(n.id, n);
  }

  // ── 分层（最长路径 = 单父链深度；断环 + 缺父容错） ──
  const layerOf = new Map<ActionNode, number>();
  const layer = (n: ActionNode): number => {
    const memo = layerOf.get(n);
    if (memo !== undefined) return memo;
    layerOf.set(n, 0); // 先置 0 断环（环上边按已知最深祖先 +1 收敛）
    let value = 0;
    const pid = n.parent_id;
    if (pid) {
      const parent = nodesById.get(pid);
      if (parent && parent !== n) value = layer(parent) + 1;
    }
    layerOf.set(n, value);
    return value;
  };
  for (const n of nodes) layer(n);

  // ── 泳道（branch 拓扑序 → 泳道号；节点归属 + 回退） ──
  const branchLane = branchLaneIndices(branches);
  const forkPoints = new Set<string>();
  const heads = new Set<string>();
  for (const branch of branches) {
    if (branch.fork_point_node_id) forkPoints.add(branch.fork_point_node_id);
    if (branch.head_node_id) heads.add(branch.head_node_id);
  }
  const laneIndexOf = new Map<ActionNode, number>();
  for (const n of nodes) {
    const branchId = n.created_on_branch_id;
    laneIndexOf.set(n, branchId ? (branchLane.get(branchId) ?? 0) : 0);
  }

  // ── 坐标（同层多节点按 (laneIndex, timestamp, id) 稳定排序；槽位防重叠） ──
  interface Geometry {
    laneIndex: number;
    layer: number;
    x: number;
    y: number;
  }
  const cells = new Map<string, { laneIndex: number; layer: number; members: ActionNode[] }>();
  for (const n of nodes) {
    const laneIndex = laneIndexOf.get(n) ?? 0;
    const l = layerOf.get(n) ?? 0;
    const key = `${laneIndex}:${l}`;
    const cell = cells.get(key) ?? { laneIndex, layer: l, members: [] };
    cell.members.push(n);
    cells.set(key, cell);
  }
  const geometryOf = new Map<ActionNode, Geometry>();
  for (const cell of cells.values()) {
    cell.members.sort(nodeKey);
    const slotHeight = laneHeight / cell.members.length;
    cell.members.forEach((n, slot) => {
      geometryOf.set(n, {
        laneIndex: cell.laneIndex,
        layer: cell.layer,
        x: cell.layer * layerGap,
        y: cell.laneIndex * laneHeight + (slot + 0.5) * slotHeight,
      });
    });
  }

  const sorted = [...nodes].sort((a, b) => {
    const ga = geometryOf.get(a);
    const gb = geometryOf.get(b);
    return (
      (ga?.layer ?? 0) - (gb?.layer ?? 0) ||
      (ga?.laneIndex ?? 0) - (gb?.laneIndex ?? 0) ||
      nodeKey(a, b)
    );
  });

  const layoutNodes: LayoutNode[] = sorted.map((n) => {
    const g = geometryOf.get(n);
    return {
      node: n,
      x: g?.x ?? 0,
      y: g?.y ?? 0,
      laneIndex: g?.laneIndex ?? 0,
      isBranchPoint: n.id ? forkPoints.has(n.id) : false,
      isHead: n.id ? heads.has(n.id) : false,
    };
  });

  const layoutById = new Map<string, LayoutNode>();
  for (const ln of layoutNodes) {
    if (ln.node.id) layoutById.set(ln.node.id, ln);
  }

  // ── 边（父右缘 → 子左缘贝塞尔；根/缺父省略） ──
  const edges: LayoutEdge[] = [];
  for (const ln of layoutNodes) {
    const pid = ln.node.parent_id;
    if (!pid) continue;
    const from = layoutById.get(pid);
    if (!from) continue;
    const x1 = round2(from.x + nodeWidth);
    const y1 = round2(from.y);
    const x2 = round2(ln.x);
    const y2 = round2(ln.y);
    const gap = round2(Math.max((ln.x - (from.x + nodeWidth)) / 2, 1));
    edges.push({
      fromId: pid,
      toId: ln.node.id ?? "",
      path: `M ${x1} ${y1} C ${round2(x1 + gap)} ${y1}, ${round2(x2 - gap)} ${y2}, ${x2} ${y2}`,
      laneChange: from.laneIndex !== ln.laneIndex,
    });
  }

  // ── 维度 ──
  let maxLayer = 0;
  let maxLane = 0;
  for (const g of geometryOf.values()) {
    maxLayer = Math.max(maxLayer, g.layer);
    maxLane = Math.max(maxLane, g.laneIndex);
  }

  return {
    nodes: layoutNodes,
    edges,
    width: maxLayer * layerGap + nodeWidth,
    height: (maxLane + 1) * laneHeight,
  };
}
