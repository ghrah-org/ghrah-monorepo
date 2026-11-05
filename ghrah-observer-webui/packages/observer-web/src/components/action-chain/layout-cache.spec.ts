// SPDX-FileCopyrightText: 2026 chenxya <chenxya@ghrah.org>
//
// SPDX-License-Identifier: Apache-2.0

import type { ActionNode, BranchInfoPayload } from "@ghrah/protocol";
import { ActionNodeSchema } from "@ghrah/protocol";
import { describe, expect, it } from "vitest";
import { createLayoutCache } from "./layout-cache.js";
import { layoutGraph } from "./layout-graph.js";

function node(overrides: Partial<ActionNode> = {}): ActionNode {
  return ActionNodeSchema.parse(overrides);
}

function branch(overrides: Partial<BranchInfoPayload>): BranchInfoPayload {
  return {
    branch_id: "main",
    session_id: "s1",
    name: "main",
    head_node_id: "",
    lifecycle: "open",
    parent_branch_id: null,
    fork_point_node_id: null,
    created_at: "",
    metadata: {},
    ...overrides,
  };
}

function ts(minutes: number): string {
  return `2026-01-01T00:${String(minutes).padStart(2, "0")}:00Z`;
}

describe("createLayoutCache", () => {
  it("append-only growth reuses old LayoutNode references (千节点增量)", () => {
    const cache = createLayoutCache();
    const branches = [branch({ branch_id: "main", head_node_id: "n1", created_at: ts(0) })];
    const base = [
      node({ id: "n1", parent_id: null, timestamp: ts(1), created_on_branch_id: "main" }),
    ];
    const g0 = cache.compute("k", base, branches);
    expect(g0.nodes).toHaveLength(1);

    // 追加千节点线性链：逐次旧引用复用
    let growing = base;
    let graph = g0;
    for (let i = 2; i <= 1000; i += 1) {
      growing = [
        ...growing,
        node({
          id: `n${i}`,
          parent_id: `n${i - 1}`,
          timestamp: ts(i),
          created_on_branch_id: "main",
        }),
      ];
      graph = cache.compute("k", growing, branches);
    }
    expect(graph.nodes).toHaveLength(1000);
    // 全部旧 LayoutNode 引用逐位 ===（Vue keyed diff 跳过，DOM 零重渲）
    const full = layoutGraph(growing, branches);
    expect(graph.nodes.map((n) => n.node.id)).toEqual(full.nodes.map((n) => n.node.id));
    expect(graph.nodes.map((n) => n.x)).toEqual(full.nodes.map((n) => n.x));
    expect(graph.nodes.map((n) => n.y)).toEqual(full.nodes.map((n) => n.y));
    expect(graph.nodes.map((n) => n.laneIndex)).toEqual(full.nodes.map((n) => n.laneIndex));
    expect(graph.edges.map((e) => `${e.fromId}->${e.toId}`)).toEqual(
      full.edges.map((e) => `${e.fromId}->${e.toId}`),
    );
    expect(graph.width).toBe(full.width);
    expect(graph.height).toBe(full.height);
    // 首节点对象自始至终未被替换
    expect(graph.nodes[0]).toBe(g0.nodes[0]);
  });

  it("head advance only patches markers: at most 2 node objects replaced", () => {
    const cache = createLayoutCache();
    const nodes1 = [
      node({ id: "a", parent_id: null, timestamp: ts(1), created_on_branch_id: "main" }),
      node({ id: "b", parent_id: "a", timestamp: ts(2), created_on_branch_id: "main" }),
    ];
    const branches1 = [branch({ branch_id: "main", head_node_id: "a", created_at: ts(0) })];
    const g0 = cache.compute("k", nodes1, branches1);
    expect(g0.nodes[0]!.isHead).toBe(true);

    // head 前移 a → c（随 commit 追加节点 c）
    const nodes2 = [
      ...nodes1,
      node({ id: "c", parent_id: "b", timestamp: ts(3), created_on_branch_id: "main" }),
    ];
    const branches2 = [branch({ branch_id: "main", head_node_id: "c", created_at: ts(0) })];
    const g1 = cache.compute("k", nodes2, branches2);

    const byId = new Map(g1.nodes.map((n) => [n.node.id!, n]));
    expect(byId.get("c")!.isHead).toBe(true);
    expect(byId.get("a")!.isHead).toBe(false);
    // 未涉及标记变化的 b 引用原样复用；a 因标记翻转被克隆（仅 1 个旧对象替换）
    expect(byId.get("b")).toBe(g0.nodes[1]);
    expect(byId.get("a")).not.toBe(g0.nodes[0]);
    expect(byId.get("a")!.x).toBe(g0.nodes[0]!.x);
  });

  it("new branch appends a lane: cross-lane edge + old refs reused + fork patch", () => {
    const cache = createLayoutCache();
    const nodes1 = [
      node({ id: "m1", parent_id: null, timestamp: ts(1), created_on_branch_id: "main" }),
      node({ id: "m2", parent_id: "m1", timestamp: ts(2), created_on_branch_id: "main" }),
    ];
    const branches1 = [branch({ branch_id: "main", head_node_id: "m2", created_at: ts(0) })];
    const g0 = cache.compute("k", nodes1, branches1);

    // 新 branch（最晚 created_at）+ 其链上节点：只追加泳道
    const nodes2 = [
      ...nodes1,
      node({ id: "r1", parent_id: "m2", timestamp: ts(3), created_on_branch_id: "retry" }),
    ];
    const branches2 = [
      branches1[0]!,
      branch({
        branch_id: "retry",
        name: "retry-1",
        head_node_id: "r1",
        parent_branch_id: "main",
        fork_point_node_id: "m2",
        created_at: ts(9),
      }),
    ];
    const g1 = cache.compute("k", nodes2, branches2);

    const byId = new Map(g1.nodes.map((n) => [n.node.id!, n]));
    expect(byId.get("r1")!.laneIndex).toBe(1);
    expect(byId.get("m1")!.laneIndex).toBe(0);
    // fork 标记补丁：m2 克隆为 isBranchPoint
    expect(byId.get("m2")!.isBranchPoint).toBe(true);
    expect(byId.get("m2")).not.toBe(g0.nodes[1]);
    // 未受影响节点引用复用
    expect(byId.get("m1")).toBe(g0.nodes[0]);
    // 跨泳道边 + 维度
    const forkEdge = g1.edges.find((e) => e.toId === "r1")!;
    expect(forkEdge.laneChange).toBe(true);
    expect(g1.height).toBe(2 * 96);

    // 与全量结果一致
    const full = layoutGraph(nodes2, branches2);
    expect(g1.nodes.map((n) => [n.node.id, n.x, n.y, n.laneIndex])).toEqual(
      full.nodes.map((n) => [n.node.id, n.x, n.y, n.laneIndex]),
    );
  });

  it("identical input returns cached graph with same references (no rebuild)", () => {
    const cache = createLayoutCache();
    const nodes = [
      node({ id: "a", parent_id: null, timestamp: ts(1), created_on_branch_id: "main" }),
      node({ id: "b", parent_id: "a", timestamp: ts(2), created_on_branch_id: "main" }),
    ];
    const branches = [branch({ branch_id: "main", head_node_id: "b", created_at: ts(0) })];
    const g0 = cache.compute("k", nodes, branches);
    const g1 = cache.compute("k", nodes, branches);
    expect(g1).toBe(g0);
  });

  it("out-of-order append (earlier sort position) falls back to full rebuild", () => {
    const cache = createLayoutCache();
    const nodes1 = [
      node({ id: "a", parent_id: null, timestamp: ts(10), created_on_branch_id: "main" }),
    ];
    const branches = [branch({ branch_id: "main", head_node_id: "a", created_at: ts(0) })];
    const g0 = cache.compute("k", nodes1, branches);

    // 追加时间戳更早的根：visible 序列按 (iteration,timestamp,id) 排序会插到 a 之前 → 前缀失配
    const backfill = node({
      id: "z9",
      parent_id: null,
      timestamp: ts(5),
      created_on_branch_id: "main",
    });
    const nodes2 = [backfill, ...nodes1]; // 模拟 getVisibleNodes 重排后的序列
    const g1 = cache.compute("k", nodes2, branches);
    const full = layoutGraph(nodes2, branches);
    expect(g1.nodes.map((n) => [n.node.id, n.x, n.y])).toEqual(
      full.nodes.map((n) => [n.node.id, n.x, n.y]),
    );
    // 前缀失配 → 全量重建（新数组/新对象）
    expect(g1.nodes[1]).not.toBe(g0.nodes[0]);
  });

  it("shrink falls back to full rebuild", () => {
    const cache = createLayoutCache();
    const nodes = [
      node({ id: "a", parent_id: null, timestamp: ts(1), created_on_branch_id: "main" }),
      node({ id: "b", parent_id: "a", timestamp: ts(2), created_on_branch_id: "main" }),
    ];
    const branches = [branch({ branch_id: "main", head_node_id: "b", created_at: ts(0) })];
    cache.compute("k", nodes, branches);
    const g1 = cache.compute("k", nodes.slice(0, 1), branches);
    expect(g1.nodes.map((n) => n.node.id)).toEqual(["a"]);
  });

  it("branch backfill with earlier created_at shifts lanes → falls back (correctness)", () => {
    const cache = createLayoutCache();
    const nodes1 = [
      node({ id: "m1", parent_id: null, timestamp: ts(1), created_on_branch_id: "main" }),
      node({ id: "r1", parent_id: "m1", timestamp: ts(2), created_on_branch_id: "retry" }),
    ];
    // 先见到 retry（created_at 较晚）占据泳道 1
    const branches1 = [
      branch({ branch_id: "main", head_node_id: "m1", created_at: ts(1) }),
      branch({
        branch_id: "retry",
        head_node_id: "r1",
        parent_branch_id: "main",
        created_at: ts(5),
      }),
    ];
    cache.compute("k", nodes1, branches1);

    // 回填更早创建的同父 branch：拓扑序中 retry 之前插入 → 泳道重排 → 旧泳道漂移
    const branches2 = [
      branch({ branch_id: "main", head_node_id: "m1", created_at: ts(1) }),
      branch({
        branch_id: "early",
        head_node_id: "m1",
        parent_branch_id: "main",
        created_at: ts(3),
      }),
      branch({
        branch_id: "retry",
        head_node_id: "r1",
        parent_branch_id: "main",
        created_at: ts(5),
      }),
    ];
    const g1 = cache.compute("k", nodes1, branches2);
    const full = layoutGraph(nodes1, branches2);
    expect(g1.nodes.map((n) => [n.node.id, n.laneIndex])).toEqual(
      full.nodes.map((n) => [n.node.id, n.laneIndex]),
    );
  });

  it("same-cell append (would reslot old nodes) falls back to full rebuild", () => {
    const cache = createLayoutCache();
    const nodes1 = [
      node({ id: "root", parent_id: null, timestamp: ts(1), created_on_branch_id: "main" }),
    ];
    const branches = [branch({ branch_id: "main", head_node_id: "root", created_at: ts(0) })];
    cache.compute("k", nodes1, branches);
    // 追加第二个根：同 (lane 0, layer 0) cell → 旧节点槽位会被细分重排 → 全量
    const nodes2 = [
      ...nodes1,
      node({ id: "root2", parent_id: null, timestamp: ts(2), created_on_branch_id: "main" }),
    ];
    const g1 = cache.compute("k", nodes2, branches);
    const full = layoutGraph(nodes2, branches);
    expect(g1.nodes.map((n) => [n.node.id, n.y])).toEqual(full.nodes.map((n) => [n.node.id, n.y]));
  });

  it("parent appearing later in the same batch falls back to full rebuild", () => {
    const cache = createLayoutCache();
    const nodes1 = [
      node({ id: "a", parent_id: null, timestamp: ts(1), created_on_branch_id: "main" }),
    ];
    const branches = [branch({ branch_id: "main", head_node_id: "a", created_at: ts(0) })];
    cache.compute("k", nodes1, branches);
    // 同帧子先于父到达：子的 parent 在批次中更晚 → 无法按序解析层 → 全量
    const child = node({ id: "c", parent_id: "p", timestamp: ts(2), created_on_branch_id: "main" });
    const parent = node({
      id: "p",
      parent_id: "a",
      timestamp: ts(3),
      created_on_branch_id: "main",
    });
    const g1 = cache.compute("k", [...nodes1, child, parent], branches);
    const full = layoutGraph([...nodes1, child, parent], branches);
    expect(g1.nodes.map((n) => [n.node.id, n.x])).toEqual(full.nodes.map((n) => [n.node.id, n.x]));
  });

  it("keys isolate scopes: switching agents does not cross-contaminate", () => {
    const cache = createLayoutCache();
    const nodesA = [
      node({ id: "a1", parent_id: null, timestamp: ts(1), created_on_branch_id: "main" }),
    ];
    const nodesB = [
      node({ id: "b1", parent_id: null, timestamp: ts(1), created_on_branch_id: "main" }),
    ];
    const branches = [branch({ branch_id: "main", head_node_id: "a1", created_at: ts(0) })];
    const gA = cache.compute("A", nodesA, branches);
    cache.compute("B", nodesB, [branch({ head_node_id: "b1" })]);
    // 回到 A：同输入直接命中缓存（引用相同 = 未重建未串扰）
    const gA2 = cache.compute("A", nodesA, branches);
    expect(gA2).toBe(gA);
  });

  it("evicts least-recently-used keys beyond limit", () => {
    const cache = createLayoutCache(2);
    const mk = (id: string) => [
      node({ id, parent_id: null, timestamp: ts(1), created_on_branch_id: "main" }),
    ];
    const branches = [branch({ branch_id: "main", created_at: ts(0) })];
    const g1 = cache.compute("a", mk("n1"), branches);
    cache.compute("b", mk("n2"), branches);
    cache.compute("c", mk("n3"), branches); // 超限淘汰 a
    const g1b = cache.compute("a", mk("n1"), branches); // 重建（新对象）
    expect(g1b).not.toBe(g1);
    expect(g1b.nodes.map((n) => n.node.id)).toEqual(["n1"]);
  });
});
