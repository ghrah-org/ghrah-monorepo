// SPDX-FileCopyrightText: 2026 chenxya <chenxya@ghrah.org>
//
// SPDX-License-Identifier: Apache-2.0

// @vitest-environment happy-dom

import type { SessionProjection } from "@ghrah/observer-core";
import { mount } from "@vue/test-utils";
import { describe, expect, it } from "vitest";
import ActionChainSessionPicker from "./action-chain-session-picker.vue";

function projection(id: string, name?: string): SessionProjection {
  return {
    target: { projectId: "p1", agentId: "a1", agentName: "alpha", sessionId: id },
    info: {
      session_id: id,
      agent_name: "alpha",
      root_node_id: "root",
      active_branch_id: "main",
      ...(name ? { name } : {}),
    } as SessionProjection["info"],
  };
}

describe("ActionChainSessionPicker", () => {
  const base = {
    sessions: [projection("s1", "one"), projection("s2", "two")],
    viewedSessionId: "s1",
    runtimeSessionId: "s2",
  };

  it("plain selection only emits view; no activate", async () => {
    const wrapper = mount(ActionChainSessionPicker, { props: base });
    await wrapper.get<HTMLSelectElement>(".chain-session-select").setValue("s2");
    // D1：普通点击只查看（view 事件），组件自身不发任何命令事件
    expect(wrapper.emitted("view")).toEqual([["s2"]]);
    expect(wrapper.emitted("activate")).toBeUndefined();
    expect(wrapper.emitted("archive")).toBeUndefined();
    expect(wrapper.emitted("remove")).toBeUndefined();
  });

  it("marks the running session in the option label", () => {
    const wrapper = mount(ActionChainSessionPicker, { props: base });
    const options = wrapper.findAll("option");
    // options[0] = 占位「选择 Session」
    expect(options[2].text()).toContain("two");
    expect(options[2].text()).toContain("running");
    expect(options[1].text()).not.toContain("running");
  });

  it("activate button only for non-runtime viewed session", async () => {
    const wrapper = mount(ActionChainSessionPicker, { props: base });
    await wrapper.get("button.chain-activate-session").trigger("click");
    expect(wrapper.emitted("activate")).toHaveLength(1);

    // viewed = runtime 时按钮消失
    await wrapper.setProps({ viewedSessionId: "s2" });
    expect(wrapper.find("button.chain-activate-session").exists()).toBe(false);
  });

  it("archive emits for viewed session", async () => {
    const wrapper = mount(ActionChainSessionPicker, { props: base });
    await wrapper.get("button.chain-archive-session").trigger("click");
    expect(wrapper.emitted("archive")).toHaveLength(1);
  });

  it("delete disabled for the running session", async () => {
    const wrapper = mount(ActionChainSessionPicker, { props: base });
    // viewed(s1) ≠ runtime(s2)：可删
    const btn = wrapper.get<HTMLButtonElement>("button.chain-delete-session");
    expect(btn.attributes("disabled")).toBeUndefined();
    await btn.trigger("click");
    expect(wrapper.emitted("remove")).toHaveLength(1);

    // viewed = runtime：按钮禁用（后端拒绝删活跃对象，前端先禁）
    await wrapper.setProps({ viewedSessionId: "s2" });
    const disabledBtn = wrapper.get<HTMLButtonElement>("button.chain-delete-session");
    expect(disabledBtn.attributes("disabled")).toBeDefined();
    expect(wrapper.find("button.chain-archive-session").exists()).toBe(true);
  });

  it("new session emits create", async () => {
    const wrapper = mount(ActionChainSessionPicker, { props: base });
    await wrapper.get("button.chain-new-session").trigger("click");
    expect(wrapper.emitted("create")).toHaveLength(1);
  });
});
