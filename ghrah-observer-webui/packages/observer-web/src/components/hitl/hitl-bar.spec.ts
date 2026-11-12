// SPDX-FileCopyrightText: 2026 chenxya <chenxya@ghrah.org>
//
// SPDX-License-Identifier: Apache-2.0

// @vitest-environment happy-dom

import { useHitlStore } from "@ghrah/observer-core";
import { mount } from "@vue/test-utils";
import { createPinia, setActivePinia } from "pinia";
import { beforeEach, describe, expect, it, vi } from "vitest";

const sendHitlResponseMock = vi.fn();
const clearAgentMock = vi.fn();
vi.mock("@/composables/useObserver", () => ({
  useObserver: () => ({ sendHitlResponse: sendHitlResponseMock, clearAgent: clearAgentMock }),
}));

import HitlBar from "./hitl-bar.vue";

function mountBar() {
  return mount(HitlBar);
}

describe("HitlBar", () => {
  beforeEach(() => {
    setActivePinia(createPinia());
    sendHitlResponseMock.mockReset().mockResolvedValue(undefined);
  });

  it("没有待审批时不渲染任何东西", () => {
    const wrapper = mountBar();
    expect(wrapper.find(".hitl-bar").exists()).toBe(false);
    expect(wrapper.text()).toBe("");
  });

  it("一行摘要展示数量、能力与 Agent，并收起完整队列", () => {
    const hitl = useHitlStore();
    hitl.onHitlRequest({
      promise_id: "p-1",
      project_id: "p1",
      agent_id: "a1",
      agent_name: "backend",
      ability_name: "deploy",
      tool_args: { target: "staging", ref: "main" },
      context: {},
      reason: "",
    });
    const wrapper = mountBar();
    expect(wrapper.find(".hitl-bar").exists()).toBe(true);
    expect(wrapper.text()).toContain("1 pending approval");
    expect(wrapper.text()).toMatch(/deploy/);
    expect(wrapper.text()).toContain("backend");
    expect(wrapper.find("#hitl-bar-panel").exists()).toBe(false);
  });

  it("批准：对当前待审批项发送 hitl_response(true)", async () => {
    const hitl = useHitlStore();
    hitl.onHitlRequest({
      promise_id: "p-1",
      project_id: "p1",
      agent_id: "a1",
      agent_name: "backend",
      ability_name: "deploy",
      tool_args: {},
      context: {},
      reason: "",
    });
    const wrapper = mountBar();
    const approve = wrapper.findAll("button").find((button) => button.text() === "Approve")!;
    await approve.trigger("click");
    expect(sendHitlResponseMock).toHaveBeenCalledWith("p-1", true);
  });

  it("详情展开完整队列，拒绝先展开再填写理由", async () => {
    const hitl = useHitlStore();
    hitl.onHitlRequest({
      promise_id: "p-1",
      project_id: "p1",
      agent_id: "a1",
      agent_name: "backend",
      ability_name: "deploy",
      tool_args: {},
      context: {},
      reason: "",
    });
    const wrapper = mountBar();
    const details = wrapper.findAll("button").find((button) => button.text() === "Details")!;
    await details.trigger("click");
    expect(wrapper.find("#hitl-bar-panel").exists()).toBe(true);
    expect(wrapper.text()).toContain("HITL Inbox");
  });
});
