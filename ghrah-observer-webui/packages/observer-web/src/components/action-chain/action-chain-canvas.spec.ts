// SPDX-FileCopyrightText: 2026 chenxya <chenxya@ghrah.org>
//
// SPDX-License-Identifier: Apache-2.0

// @vitest-environment happy-dom

import { type ActionNode, ActionNodeSchema, type BranchInfoPayload } from "@ghrah/protocol";
import { mount } from "@vue/test-utils";
import { describe, expect, it } from "vitest";
import ActionChainCanvas from "./action-chain-canvas.vue";
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

function graphOf(
  nodes: Partial<ActionNode>[],
  branches: Partial<BranchInfoPayload>[] = [],
): ReturnType<typeof layoutGraph> {
  return layoutGraph(
    nodes.map((item) => node(item)),
    branches.map((item) => branch(item)),
  );
}

describe("ActionChainCanvas", () => {
  it("renders nodes and edges consistent with the graph", () => {
    const graph = graphOf([
      { id: "a", parent_id: null, timestamp: "t1" },
      { id: "b", parent_id: "a", timestamp: "t2" },
    ]);
    const wrapper = mount(ActionChainCanvas, { props: { graph, selectedNodeId: null } });
    expect(wrapper.findAll(".ac-node")).toHaveLength(2);
    expect(wrapper.findAll(".ac-edge")).toHaveLength(1);
  });

  it("emits select with nodeId on node click", async () => {
    const graph = graphOf([{ id: "a", parent_id: null, timestamp: "t1" }]);
    const wrapper = mount(ActionChainCanvas, { props: { graph, selectedNodeId: null } });
    await wrapper.find(".ac-node").trigger("click");
    expect(wrapper.emitted("select")?.[0]).toEqual(["a"]);
  });

  it("emits select(null) on background click", async () => {
    const graph = graphOf([{ id: "a", parent_id: null, timestamp: "t1" }]);
    const wrapper = mount(ActionChainCanvas, { props: { graph, selectedNodeId: null } });
    await wrapper.find(".ac-canvas-viewport").trigger("click");
    expect(wrapper.emitted("select")?.[0]).toEqual([null]);
  });

  it("marks the selected node with the selected class", () => {
    const graph = graphOf([
      { id: "a", parent_id: null, timestamp: "t1" },
      { id: "b", parent_id: "a", timestamp: "t2" },
    ]);
    const wrapper = mount(ActionChainCanvas, { props: { graph, selectedNodeId: "b" } });
    const selected = wrapper.findAll(".ac-node").filter((n) => n.classes().includes("selected"));
    expect(selected).toHaveLength(1);
    expect(selected[0].text()).toContain("iter=");
  });

  it("draws lane guides and lane-change edges for forked branches", () => {
    const graph = graphOf(
      [
        { id: "root", parent_id: null, timestamp: "t0", created_on_branch_id: "main" },
        { id: "m1", parent_id: "root", timestamp: "t1", created_on_branch_id: "main" },
        { id: "r1", parent_id: "root", timestamp: "t2", created_on_branch_id: "retry" },
      ],
      [
        { branch_id: "main", name: "main", head_node_id: "m1" },
        {
          branch_id: "retry",
          name: "retry",
          head_node_id: "r1",
          parent_branch_id: "main",
          fork_point_node_id: "root",
        },
      ],
    );
    const wrapper = mount(ActionChainCanvas, { props: { graph, selectedNodeId: null } });
    expect(wrapper.findAll(".ac-lane-guide")).toHaveLength(1);
    expect(wrapper.findAll(".ac-edge--lane-change")).toHaveLength(1);
    const forkBadge = wrapper.findAll(".ac-node-badge--fork");
    expect(forkBadge).toHaveLength(1);
  });

  it("renders the ability legend", () => {
    const graph = graphOf([{ id: "a", parent_id: null, timestamp: "t1" }]);
    const wrapper = mount(ActionChainCanvas, { props: { graph, selectedNodeId: null } });
    expect(wrapper.findAll(".ac-legend-item")).toHaveLength(4);
  });

  it("does not clip overflow: wide SVG must overflow toward the outer scroller", () => {
    // 横向滚动回归：视口是唯一滚动容器（overflow:auto 由 style.css 提供），
    // 宽 SVG 撑开视口 scrollWidth 出现横向滚动条。happy-dom 不做布局，
    // 此处断言 SVG 宽度如实传入且视口元素存在（CSS 契约另由 lint 门禁约束）。
    const nodes = Array.from({ length: 10 }, (_, i) => ({
      id: `n${i}`,
      parent_id: i === 0 ? null : `n${i - 1}`,
      timestamp: `t${i}`,
    }));
    const graph = graphOf(nodes);
    expect(graph.width).toBeGreaterThan(2000);
    const wrapper = mount(ActionChainCanvas, { props: { graph, selectedNodeId: null } });
    const viewport = wrapper.find(".ac-canvas-viewport");
    expect(viewport.exists()).toBe(true);
    const style = viewport.attributes("style") ?? "";
    expect(style).not.toMatch(/overflow/i);
    const svg = wrapper.find(".ac-canvas-svg");
    expect(Number(svg.attributes("width"))).toBe(graph.width);
  });

  it("keeps the legend outside the scrolling viewport", () => {
    // 图例在滚动视口之外（静态行）：横向滚动画布时图例不随 scrollLeft 移动
    const graph = graphOf([{ id: "a", parent_id: null, timestamp: "t1" }]);
    const wrapper = mount(ActionChainCanvas, { props: { graph, selectedNodeId: null } });
    const legend = wrapper.find(".ac-legend");
    expect(legend.exists()).toBe(true);
    // 图例不得是视口的子元素（DOM 层级契约）
    expect(legend.element.parentElement?.classList.contains("ac-canvas-viewport")).toBe(false);
  });

  it("exposes the viewport element for scroll restore", () => {
    const graph = graphOf([{ id: "a", parent_id: null, timestamp: "t1" }]);
    const wrapper = mount(ActionChainCanvas, { props: { graph, selectedNodeId: null } });
    expect(wrapper.vm.viewport).toBe(wrapper.find(".ac-canvas-viewport").element);
  });
});
