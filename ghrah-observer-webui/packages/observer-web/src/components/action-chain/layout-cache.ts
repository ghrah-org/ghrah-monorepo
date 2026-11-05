// SPDX-FileCopyrightText: 2026 chenxya <chenxya@ghrah.org>
//
// SPDX-License-Identifier: Apache-2.0

import type { ActionNode, BranchInfoPayload } from "@ghrah/protocol";
import {
  branchLaneIndices,
  type LayoutEdge,
  type LayoutGraph,
  type LayoutNode,
  layoutGraph,
  nodeKey,
} from "./layout-graph.js";

/** 坐标常量：与 layoutGraph 默认值一致（缓存路径不接自定义 options）。 */
const LANE_HEIGHT = 96;
const LAYER_GAP = 240;
const NODE_WIDTH = 200;

interface CacheEntry {
  /** 已建图的输入快照（nodes 为 append-only 引用前缀）。 */
  nodes: ActionNode[];
  branches: BranchInfoPayload[];
  graph: LayoutGraph;
  /** nodeId → layer（O(1) 父层解析；旧节点 layer 由 x/LAYER_GAP 还原）。 */
  layerOf: Map<string, number>;
}

/** 引用前缀：旧 nodes 必须与新序列逐位同引用（append-only 增长）。 */
function prefixMatches(prefix: ActionNode[], nodes: ActionNode[]): boolean {
  if (prefix.length > nodes.length) return false;
  for (let i = 0; i < prefix.length; i += 1) {
    if (prefix[i] !== nodes[i]) return false;
  }
  return true;
}

/**
 * 泳道序稳定：旧 branch 在新 branch 集合下的泳道号必须全部不变。
 *
 * 前提（layout-graph 拓扑性质）：新 branch 以最晚 created_at 加入且父 branch 已在集合中时，
 * 泳道号只追加、旧泳道不变；早建时间回填/重排会移动旧泳道 → 由本检查捕获回退全量。
 */
function lanesStable(oldBranches: BranchInfoPayload[], next: Map<string, number>): boolean {
  const old = branchLaneIndices(oldBranches);
  for (const [branchId, lane] of old) {
    if (next.get(branchId) !== lane) return false;
  }
  return true;
}

function round2(value: number): number {
  return Math.round(value * 100) / 100;
}

/** branch 快照逐字段相等（head 前移/rename 等会克隆 info 对象，引用比较不可靠）。 */
function branchesEqual(a: BranchInfoPayload[], b: BranchInfoPayload[]): boolean {
  if (a.length !== b.length) return false;
  for (let i = 0; i < a.length; i += 1) {
    const x = a[i]!;
    const y = b[i]!;
    if (
      x.branch_id !== y.branch_id ||
      x.head_node_id !== y.head_node_id ||
      x.fork_point_node_id !== y.fork_point_node_id ||
      x.parent_branch_id !== y.parent_branch_id ||
      x.created_at !== y.created_at
    ) {
      return false;
    }
  }
  return true;
}

/**
 * per-scope 画布布局缓存（性能红线 2：前缀检测 + 引用复用 + 失配回退全量 + LRU 范式）：
 * 节点 append-only 增长且 branch 泳道序稳定时，增量补建新节点的布局对象，
 * 旧 `LayoutNode`/`LayoutEdge` 引用原样复用（Vue keyed diff 跳过，DOM 零重渲），
 * `isHead`/`isBranchPoint` 标记做克隆 diff 补丁（head 前移仅触及 2 个节点对象）；
 * 其余变化（前缀失配/泳道漂移/槽位冲突/收缩）一律回退全量 `layoutGraph`——
 * 正确性优先，永不抛错。LRU 上限 `limit`（切换 agent/session 不清缓存）。
 */
export function createLayoutCache(limit = 20) {
  const cache = new Map<string, CacheEntry>();

  function evict(): void {
    while (cache.size > limit) {
      const oldest = cache.keys().next().value;
      if (oldest === undefined) break;
      cache.delete(oldest);
    }
  }

  function fullBuild(entry: CacheEntry, nodes: ActionNode[], branches: BranchInfoPayload[]) {
    const graph = layoutGraph(nodes, branches);
    const layerOf = new Map<string, number>();
    for (const ln of graph.nodes) {
      if (ln.node.id) layerOf.set(ln.node.id, Math.round(ln.x / LAYER_GAP));
    }
    entry.nodes = [...nodes];
    entry.branches = [...branches];
    entry.graph = graph;
    entry.layerOf = layerOf;
    return graph;
  }

  /**
   * 增量补建：返回 null 表示无法增量（调用方回退全量）。
   *
   * 守卫：新节点缺 id / 父节点在本批次中晚于自己出现 / 落入旧节点已占用的
   * (lane, layer) 槽位（槽位细分将重排旧节点）→ null。
   */
  function appendNodes(
    entry: CacheEntry,
    nodes: ActionNode[],
    branches: BranchInfoPayload[],
  ): LayoutGraph | null {
    const appended = nodes.slice(entry.nodes.length);
    if (appended.some((n) => !n.id)) return null;

    // ── 层解析（旧 layer 由缓存还原；新节点按追加序 memo，与全量递归语义一致） ──
    const layerOf = new Map(entry.layerOf);
    const batchIndex = new Map<string, number>();
    appended.forEach((n, i) => batchIndex.set(n.id!, i));
    for (let i = 0; i < appended.length; i += 1) {
      const n = appended[i]!;
      let steps = 0;
      let pid = n.parent_id;
      const seen = new Set<string>([n.id!]);
      let resolved = false;
      while (pid) {
        const known = layerOf.get(pid);
        if (known !== undefined) {
          layerOf.set(n.id!, known + steps + 1);
          resolved = true;
          break;
        }
        if (seen.has(pid)) break; // 环 → 按 root 收敛（全量语义：断环为根）
        if (batchIndex.has(pid) && batchIndex.get(pid)! > i) return null; // 父晚于自己 → 全量
        seen.add(pid);
        steps += 1;
        pid = nodes.find((item) => item.id === pid)?.parent_id ?? null;
      }
      if (!resolved) layerOf.set(n.id!, 0); // 缺父 → 层 0（与全量一致）
    }

    // ── 泳道 + 槽位守卫（新节点不得落入旧节点已占用的 cell） ──
    const lanes = branchLaneIndices(branches);
    const occupied = new Set<string>();
    for (const ln of entry.graph.nodes)
      occupied.add(`${ln.laneIndex}:${Math.round(ln.x / LAYER_GAP)}`);
    const cells = new Map<string, { laneIndex: number; layer: number; members: ActionNode[] }>();
    for (const n of appended) {
      const laneIndex = n.created_on_branch_id ? (lanes.get(n.created_on_branch_id) ?? 0) : 0;
      const layer = layerOf.get(n.id!) ?? 0;
      const key = `${laneIndex}:${layer}`;
      if (occupied.has(key)) return null; // 槽位冲突 → 旧节点会被重排 → 全量
      occupied.add(key);
      const cell = cells.get(key) ?? { laneIndex, layer, members: [] };
      cell.members.push(n);
      cells.set(key, cell);
    }

    // ── 新节点几何（同 cell 多新节点按 nodeKey 分槽，口径与全量一致） ──
    const geometryOf = new Map<string, { x: number; y: number; laneIndex: number }>();
    for (const cell of cells.values()) {
      cell.members.sort(nodeKey);
      const slotHeight = LANE_HEIGHT / cell.members.length;
      cell.members.forEach((n, slot) => {
        geometryOf.set(n.id!, {
          laneIndex: cell.laneIndex,
          x: cell.layer * LAYER_GAP,
          y: cell.laneIndex * LANE_HEIGHT + (slot + 0.5) * slotHeight,
        });
      });
    }

    // ── 标记集合（fork/head 从 branch 快照重建） ──
    const newFork = new Set<string>();
    const newHead = new Set<string>();
    for (const branch of branches) {
      if (branch.fork_point_node_id) newFork.add(branch.fork_point_node_id);
      if (branch.head_node_id) newHead.add(branch.head_node_id);
    }

    // ── 新 LayoutNode + 旧对象复用（标记 diff 克隆补丁） ──
    const fresh: LayoutNode[] = appended.map((n) => {
      const g = geometryOf.get(n.id!);
      return {
        node: n,
        x: g?.x ?? 0,
        y: g?.y ?? 0,
        laneIndex: g?.laneIndex ?? 0,
        isBranchPoint: newFork.has(n.id!),
        isHead: newHead.has(n.id!),
      };
    });
    const reused = entry.graph.nodes.map((ln) => {
      const id = ln.node.id;
      if (!id) return ln;
      const fork = newFork.has(id);
      const head = newHead.has(id);
      return fork === ln.isBranchPoint && head === ln.isHead
        ? ln
        : { ...ln, isBranchPoint: fork, isHead: head };
    });

    // 输出序与全量同比较器（layer → lane → nodeKey）；旧节点 x/lane 不变，相对序保持
    const merged = [...reused, ...fresh].sort(
      (a, b) =>
        Math.round(a.x / LAYER_GAP) - Math.round(b.x / LAYER_GAP) ||
        a.laneIndex - b.laneIndex ||
        nodeKey(a.node, b.node),
    );

    // ── 边：旧边引用复用；新边 = 新节点入边（旧节点入边不变） ──
    const layoutById = new Map<string, LayoutNode>();
    for (const ln of merged) {
      if (ln.node.id) layoutById.set(ln.node.id, ln);
    }
    const newEdges: LayoutEdge[] = [];
    for (const n of appended) {
      const pid = n.parent_id;
      if (!pid) continue;
      const from = layoutById.get(pid);
      const to = layoutById.get(n.id!);
      if (!from || !to) continue;
      const x1 = round2(from.x + NODE_WIDTH);
      const y1 = round2(from.y);
      const x2 = round2(to.x);
      const y2 = round2(to.y);
      const gap = round2(Math.max((to.x - (from.x + NODE_WIDTH)) / 2, 1));
      newEdges.push({
        fromId: pid,
        toId: n.id!,
        path: `M ${x1} ${y1} C ${round2(x1 + gap)} ${y1}, ${round2(x2 - gap)} ${y2}, ${x2} ${y2}`,
        laneChange: from.laneIndex !== to.laneIndex,
      });
    }

    // ── 维度 ──
    let maxLayer = 0;
    let maxLane = 0;
    for (const ln of merged) {
      maxLayer = Math.max(maxLayer, Math.round(ln.x / LAYER_GAP));
      maxLane = Math.max(maxLane, ln.laneIndex);
    }

    entry.nodes = [...nodes];
    entry.branches = [...branches];
    entry.graph = {
      nodes: merged,
      edges: [...entry.graph.edges, ...newEdges],
      width: maxLayer * LAYER_GAP + NODE_WIDTH,
      height: (maxLane + 1) * LANE_HEIGHT,
    };
    entry.layerOf = layerOf;
    return entry.graph;
  }

  function compute(key: string, nodes: ActionNode[], branches: BranchInfoPayload[]): LayoutGraph {
    const hit = cache.get(key);
    if (hit) {
      cache.delete(key);
      cache.set(key, hit); // LRU touch
      // 同输入（前缀一致且等长 + branch 快照未变）→ 直接复用已建图（零重建）
      if (
        hit.nodes.length === nodes.length &&
        prefixMatches(hit.nodes, nodes) &&
        branchesEqual(hit.branches, branches)
      ) {
        return hit.graph;
      }
      const appendable =
        hit.nodes.length < nodes.length &&
        prefixMatches(hit.nodes, nodes) &&
        lanesStable(hit.branches, branchLaneIndices(branches));
      if (appendable) {
        const graph = appendNodes(hit, nodes, branches);
        if (graph) return graph;
      }
      return fullBuild(hit, nodes, branches);
    }
    const fresh: CacheEntry = {
      nodes: [],
      branches: [],
      graph: { nodes: [], edges: [], width: 0, height: 0 },
      layerOf: new Map(),
    };
    cache.set(key, fresh);
    evict();
    return fullBuild(fresh, nodes, branches);
  }

  return { compute, clear: () => cache.clear() };
}
