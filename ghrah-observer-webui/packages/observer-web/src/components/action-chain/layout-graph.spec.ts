// SPDX-FileCopyrightText: 2026 chenxya <chenxya@ghrah.org>
//
// SPDX-License-Identifier: Apache-2.0

import { type ActionNode, ActionNodeSchema, type BranchInfoPayload } from "@ghrah/protocol";
import { describe, expect, it } from "vitest";
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

describe("layoutGraph", () => {
  it("empty input → empty graph with zero dimensions", () => {
    const graph = layoutGraph([], []);
    expect(graph.nodes).toEqual([]);
    expect(graph.edges).toEqual([]);
    expect(graph.width).toBe(0);
    expect(graph.height).toBe(0);
  });

  it("single node → layer 0, lane 0, no edges, head marker", () => {
    const graph = layoutGraph(
      [node({ id: "only", parent_id: null })],
      [branch({ branch_id: "main", head_node_id: "only" })],
    );
    expect(graph.nodes).toHaveLength(1);
    const only = graph.nodes[0]!;
    expect(only.node.id).toBe("only");
    expect(only.x).toBe(0);
    expect(only.laneIndex).toBe(0);
    expect(only.isHead).toBe(true);
    expect(only.isBranchPoint).toBe(false);
    expect(graph.edges).toEqual([]);
    expect(graph.width).toBe(200);
    expect(graph.height).toBe(96);
  });

  it("linear chain: layers 0..n-1, single lane, n-1 edges, no lane change", () => {
    const nodes = [
      node({ id: "a", parent_id: null, timestamp: "t1", created_on_branch_id: "main" }),
      node({ id: "b", parent_id: "a", timestamp: "t2", created_on_branch_id: "main" }),
      node({ id: "c", parent_id: "b", timestamp: "t3", created_on_branch_id: "main" }),
    ];
    const graph = layoutGraph(nodes, [branch({ branch_id: "main", head_node_id: "c" })]);
    expect(graph.nodes.map((n) => n.node.id)).toEqual(["a", "b", "c"]);
    expect(graph.nodes.every((n) => n.laneIndex === 0)).toBe(true);
    expect(graph.edges).toHaveLength(2);
    expect(graph.edges.every((e) => e.laneChange === false)).toBe(true);
    expect(graph.edges.map((e) => `${e.fromId}->${e.toId}`)).toEqual(["a->b", "b->c"]);
  });

  it("fork: child branch sinks to new lane, fork point marked, cross-lane edge flagged", () => {
    const nodes = [
      node({
        id: "n1",
        parent_id: null,
        timestamp: "t1",
        created_on_branch_id: "main",
      }),
      node({ id: "n2", parent_id: "n1", timestamp: "t2", created_on_branch_id: "main" }),
      node({ id: "n3", parent_id: "n2", timestamp: "t3", created_on_branch_id: "main" }),
      node({ id: "r1", parent_id: "n2", timestamp: "t4", created_on_branch_id: "retry" }),
      node({ id: "r2", parent_id: "r1", timestamp: "t5", created_on_branch_id: "retry" }),
    ];
    const branches = [
      branch({
        branch_id: "main",
        head_node_id: "n3",
        created_at: "2026-01-01T00:00:00Z",
      }),
      branch({
        branch_id: "retry",
        name: "retry-1",
        head_node_id: "r2",
        parent_branch_id: "main",
        fork_point_node_id: "n2",
        created_at: "2026-01-01T00:01:00Z",
      }),
    ];
    const graph = layoutGraph(nodes, branches);

    const byId = new Map(graph.nodes.map((n) => [n.node.id ?? "", n]));
    expect(byId.get("n1")!.laneIndex).toBe(0);
    expect(byId.get("n2")!.laneIndex).toBe(0);
    expect(byId.get("n3")!.laneIndex).toBe(0);
    expect(byId.get("r1")!.laneIndex).toBe(1);
    expect(byId.get("r2")!.laneIndex).toBe(1);

    // 分叉点标记 + Head 标记
    expect(byId.get("n2")!.isBranchPoint).toBe(true);
    expect(byId.get("n1")!.isBranchPoint).toBe(false);
    expect(byId.get("n3")!.isHead).toBe(true);
    expect(byId.get("r2")!.isHead).toBe(true);
    expect(byId.get("r1")!.isHead).toBe(false);

    // 跨泳道边（n2 → r1）laneChange=true；主链边 false
    const forkEdge = graph.edges.find((e) => e.toId === "r1")!;
    expect(forkEdge.fromId).toBe("n2");
    expect(forkEdge.laneChange).toBe(true);
    expect(graph.edges.find((e) => e.toId === "n3")!.laneChange).toBe(false);
    expect(graph.edges.find((e) => e.toId === "r2")!.laneChange).toBe(false);

    // 维度：层号 0..3 × 2 泳道
    expect(graph.width).toBe(3 * 240 + 200);
    expect(graph.height).toBe(2 * 96);
    // 边路径为三次贝塞尔
    for (const edge of graph.edges) {
      expect(edge.path.startsWith("M ")).toBe(true);
      expect(edge.path).toContain("C ");
    }
  });

  it("shared ancestors appear exactly once across merged branches", () => {
    const nodes = [
      node({ id: "n1", parent_id: null, timestamp: "t1", created_on_branch_id: "main" }),
      node({ id: "n2", parent_id: "n1", timestamp: "t2", created_on_branch_id: "main" }),
      node({ id: "n3", parent_id: "n2", timestamp: "t3", created_on_branch_id: "main" }),
      node({ id: "r1", parent_id: "n2", timestamp: "t4", created_on_branch_id: "retry" }),
    ];
    const branches = [
      branch({ branch_id: "main", head_node_id: "n3", created_at: "c1" }),
      branch({
        branch_id: "retry",
        head_node_id: "r1",
        parent_branch_id: "main",
        fork_point_node_id: "n2",
        created_at: "c2",
      }),
    ];
    const graph = layoutGraph(nodes, branches);
    const ids = graph.nodes.map((n) => n.node.id);
    expect(new Set(ids).size).toBe(ids.length);
    expect(ids.filter((id) => id === "n2")).toHaveLength(1);
  });

  it("nested branches sink below their parent branch (topological lane order)", () => {
    const nodes = [
      node({ id: "m1", parent_id: null, created_on_branch_id: "main" }),
      node({ id: "m2", parent_id: "m1", created_on_branch_id: "main" }),
      node({ id: "a1", parent_id: "m2", created_on_branch_id: "retry-1" }),
      node({ id: "aa1", parent_id: "a1", created_on_branch_id: "retry-1a" }),
      node({ id: "b1", parent_id: "m2", created_on_branch_id: "retry-2" }),
    ];
    const branches = [
      branch({ branch_id: "main", head_node_id: "m2", created_at: "c1" }),
      branch({
        branch_id: "retry-1",
        head_node_id: "a1",
        parent_branch_id: "main",
        fork_point_node_id: "m2",
        created_at: "c3",
      }),
      branch({
        branch_id: "retry-1a",
        head_node_id: "aa1",
        parent_branch_id: "retry-1",
        fork_point_node_id: "a1",
        created_at: "c2",
      }),
      branch({
        branch_id: "retry-2",
        head_node_id: "b1",
        parent_branch_id: "main",
        fork_point_node_id: "m2",
        created_at: "c4",
      }),
    ];
    const graph = layoutGraph(nodes, branches);
    const lane = new Map(graph.nodes.map((n) => [n.node.id ?? "", n.laneIndex]));
    expect(lane.get("m1")).toBe(0);
    expect(lane.get("m2")).toBe(0);
    // retry-1 在 main 之下；其子 retry-1a 必须在 retry-1 之下（拓扑序，不受 created_at 干扰）
    expect(lane.get("a1")).toBe(1);
    expect(lane.get("aa1")).toBe(2);
    expect(lane.get("b1")).toBe(3);
  });

  it("nodes without branch metadata fall back to lane 0 (branch list missing)", () => {
    const nodes = [
      node({ id: "a", parent_id: null, timestamp: "t1" }),
      node({ id: "b", parent_id: "a", timestamp: "t2" }),
    ];
    const graph = layoutGraph(nodes, []);
    expect(graph.nodes.every((n) => n.laneIndex === 0)).toBe(true);
    expect(graph.height).toBe(96);
  });

  it("missing parent (orphan): treated as root layer, no incoming edge, no throw", () => {
    const nodes = [
      node({ id: "a", parent_id: null, timestamp: "t1", created_on_branch_id: "main" }),
      node({
        id: "ghost-child",
        parent_id: "ghost",
        timestamp: "t2",
        created_on_branch_id: "main",
      }),
    ];
    const graph = layoutGraph(nodes, [branch({ branch_id: "main", head_node_id: "a" })]);
    const orphan = graph.nodes.find((n) => n.node.id === "ghost-child")!;
    // 缺失父节点：无已知祖先 → 层 0；其入边省略
    expect(orphan).toBeDefined();
    expect(graph.edges.map((e) => e.toId)).toEqual([]);
  });

  it("same-layer same-lane nodes slot by (timestamp, id) without overlap", () => {
    const nodes = [
      node({ id: "r-late", parent_id: null, timestamp: "2026-01-01T10:00:00Z" }),
      node({ id: "r-early", parent_id: null, timestamp: "2026-01-01T05:00:00Z" }),
    ];
    const graph = layoutGraph(nodes, []);
    // 两个 Root 同泳道同层：按时间戳分槽，早者在上
    const early = graph.nodes.find((n) => n.node.id === "r-early")!;
    const late = graph.nodes.find((n) => n.node.id === "r-late")!;
    expect(early.y).toBeLessThan(late.y);
    expect(early.y).toBe(24); // (0 + 0.5) * (96 / 2)
    expect(late.y).toBe(72); // (1 + 0.5) * (96 / 2)
  });

  it("custom options drive coordinates and dimensions", () => {
    const nodes = [
      node({ id: "a", parent_id: null, created_on_branch_id: "main" }),
      node({ id: "b", parent_id: "a", created_on_branch_id: "main" }),
      node({ id: "r", parent_id: "a", created_on_branch_id: "retry" }),
    ];
    const branches = [
      branch({ branch_id: "main", head_node_id: "b", created_at: "c1" }),
      branch({
        branch_id: "retry",
        head_node_id: "r",
        parent_branch_id: "main",
        fork_point_node_id: "a",
        created_at: "c2",
      }),
    ];
    const graph = layoutGraph(nodes, branches, {
      nodeWidth: 100,
      laneHeight: 50,
      layerGap: 120,
    });
    const byId = new Map(graph.nodes.map((n) => [n.node.id ?? "", n]));
    expect(byId.get("a")!.x).toBe(0);
    expect(byId.get("b")!.x).toBe(120);
    expect(byId.get("r")!.x).toBe(120);
    expect(byId.get("r")!.y).toBe(50 + 25);
    expect(graph.width).toBe(1 * 120 + 100);
    expect(graph.height).toBe(2 * 50);
  });

  it("sorts output nodes by (layer, lane, timestamp, id) for stable render order", () => {
    const nodes = [
      node({ id: "n2", parent_id: "n1", timestamp: "t2", created_on_branch_id: "main" }),
      node({ id: "r1", parent_id: "n1", timestamp: "t3", created_on_branch_id: "retry" }),
      node({ id: "n1", parent_id: null, timestamp: "t1", created_on_branch_id: "main" }),
    ];
    const branches = [
      branch({ branch_id: "main", head_node_id: "n2", created_at: "c1" }),
      branch({
        branch_id: "retry",
        head_node_id: "r1",
        parent_branch_id: "main",
        fork_point_node_id: "n1",
        created_at: "c2",
      }),
    ];
    const graph = layoutGraph(nodes, branches);
    expect(graph.nodes.map((n) => n.node.id)).toEqual(["n1", "n2", "r1"]);
  });
});
