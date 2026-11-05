// SPDX-FileCopyrightText: 2026 chenxya <chenxya@ghrah.org>
//
// SPDX-License-Identifier: Apache-2.0

// @vitest-environment happy-dom

import { type ActionNode, ActionNodeSchema } from "@ghrah/protocol";
import { mount } from "@vue/test-utils";
import { describe, expect, it } from "vitest";
import ActionNodeCard from "./action-node.vue";
import { type LayoutNode, layoutGraph } from "./layout-graph.js";

function node(overrides: Partial<ActionNode> = {}): ActionNode {
  return ActionNodeSchema.parse(overrides);
}

function layoutNodeOf(n: ActionNode, extra: Partial<LayoutNode> = {}): LayoutNode {
  const graph = layoutGraph([n], []);
  const layoutNode = graph.nodes[0];
  return { ...layoutNode, ...extra };
}

function mountCard(ln: LayoutNode, selected = false) {
  return mount(ActionNodeCard, {
    props: { layoutNode: ln, selected },
  });
}

describe("ActionNodeCard", () => {
  it("applies ability class from ability_names", () => {
    const write = mountCard(layoutNodeOf(node({ id: "w1", ability_names: ["write_file"] })));
    expect(write.find(".ac-node--write").exists()).toBe(true);
    const read = mountCard(layoutNodeOf(node({ id: "r1", ability_names: ["read_file"] })));
    expect(read.find(".ac-node--read").exists()).toBe(true);
    const converse = mountCard(layoutNodeOf(node({ id: "c1", ability_names: ["conversation"] })));
    expect(converse.find(".ac-node--converse").exists()).toBe(true);
    const unknown = mountCard(layoutNodeOf(node({ id: "u1", ability_names: ["think"] })));
    expect(unknown.find(".ac-node--unknown").exists()).toBe(true);
  });

  it("renders HEAD and FORK badges from layout flags", () => {
    const ln = layoutNodeOf(node({ id: "n1" }), { isHead: true, isBranchPoint: true });
    const wrapper = mountCard(ln);
    expect(wrapper.find(".ac-node-badge--head").exists()).toBe(true);
    expect(wrapper.find(".ac-node-badge--fork").exists()).toBe(true);
  });

  it("renders no badges for plain nodes", () => {
    const wrapper = mountCard(layoutNodeOf(node({ id: "n2" })));
    expect(wrapper.find(".ac-node-badge--head").exists()).toBe(false);
    expect(wrapper.find(".ac-node-badge--fork").exists()).toBe(false);
  });

  it("shows selected state via class", () => {
    const wrapper = mountCard(layoutNodeOf(node({ id: "n3" })), true);
    expect(wrapper.find(".ac-node.selected").exists()).toBe(true);
  });

  it("emits select with node id on click", async () => {
    const wrapper = mountCard(layoutNodeOf(node({ id: "n4" })));
    await wrapper.find(".ac-node").trigger("click");
    expect(wrapper.emitted("select")?.[0]).toEqual(["n4"]);
  });

  it("renders meta line with time and iteration", () => {
    const wrapper = mountCard(
      layoutNodeOf(node({ id: "n5", timestamp: "2026-09-19T01:02:03Z", iteration: 7 })),
    );
    expect(wrapper.find(".ac-node-meta").text()).toContain("iter=7");
  });
});
