// SPDX-FileCopyrightText: 2026 chenxya <chenxya@ghrah.org>
//
// SPDX-License-Identifier: Apache-2.0

// @vitest-environment happy-dom

import type { BranchProjection } from "@ghrah/observer-core";
import { mount } from "@vue/test-utils";
import { describe, expect, it } from "vitest";
import ActionChainBranchBar from "./action-chain-branch-bar.vue";

function projection(id: string, name?: string): BranchProjection {
  return {
    target: {
      projectId: "p1",
      agentId: "a1",
      agentName: "alpha",
      sessionId: "s1",
      branchId: id,
    },
    info: {
      branch_id: id,
      session_id: "s1",
      name: name ?? id,
      head_node_id: "root",
    } as BranchProjection["info"],
  };
}

describe("ActionChainBranchBar", () => {
  const base = {
    branches: [projection("b1", "main"), projection("b2", "retry")],
    selectedValue: "b1",
    runtimeBranchId: "b2",
  };

  it("plain selection only emits view (incl. __all__); no activate", async () => {
    const wrapper = mount(ActionChainBranchBar, { props: base });
    await wrapper.get<HTMLSelectElement>(".chain-branch-select").setValue("b2");
    expect(wrapper.emitted("view")).toEqual([["b2"]]);
    expect(wrapper.emitted("activate")).toBeUndefined();

    await wrapper.get<HTMLSelectElement>(".chain-branch-select").setValue("__all__");
    expect(wrapper.emitted("view")).toEqual([["b2"], ["__all__"]]);
    // 父组件随 view 事件回写 selectedValue（受控组件）
    await wrapper.setProps({ selectedValue: "__all__" });
    // 全部 Branch 模式：无单对象动作按钮
    expect(wrapper.find("button.chain-activate-branch").exists()).toBe(false);
    expect(wrapper.find("button.chain-archive-branch").exists()).toBe(false);
    expect(wrapper.find("button.chain-delete-branch").exists()).toBe(false);
  });

  it("marks the running branch in the option label", () => {
    const wrapper = mount(ActionChainBranchBar, { props: base });
    const options = wrapper.findAll("option");
    // options[0] = 占位、options[1] = 全部 Branch
    expect(options[3].text()).toContain("retry");
    expect(options[3].text()).toContain("running");
    expect(options[2].text()).not.toContain("running");
  });

  it("activate button only for non-runtime single viewed branch", async () => {
    const wrapper = mount(ActionChainBranchBar, { props: base });
    await wrapper.get("button.chain-activate-branch").trigger("click");
    expect(wrapper.emitted("activate")).toHaveLength(1);

    await wrapper.setProps({ selectedValue: "b2" });
    expect(wrapper.find("button.chain-activate-branch").exists()).toBe(false);
  });

  it("archive emits for single viewed branch", async () => {
    const wrapper = mount(ActionChainBranchBar, { props: base });
    await wrapper.get("button.chain-archive-branch").trigger("click");
    expect(wrapper.emitted("archive")).toHaveLength(1);
  });

  it("delete disabled for the running branch", async () => {
    const wrapper = mount(ActionChainBranchBar, { props: base });
    const btn = wrapper.get<HTMLButtonElement>("button.chain-delete-branch");
    expect(btn.attributes("disabled")).toBeUndefined();
    await btn.trigger("click");
    expect(wrapper.emitted("remove")).toHaveLength(1);

    await wrapper.setProps({ selectedValue: "b2" });
    const disabledBtn = wrapper.get<HTMLButtonElement>("button.chain-delete-branch");
    expect(disabledBtn.attributes("disabled")).toBeDefined();
  });

  it("new branch emits create", async () => {
    const wrapper = mount(ActionChainBranchBar, { props: base });
    await wrapper.get("button.chain-new-branch").trigger("click");
    expect(wrapper.emitted("create")).toHaveLength(1);
  });
});
