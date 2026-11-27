// SPDX-FileCopyrightText: 2026 chenxya <chenxya@ghrah.org>
//
// SPDX-License-Identifier: Apache-2.0

// @vitest-environment happy-dom
import { useHitlStore } from "@ghrah/observer-core";
import { HITLRequestPayloadSchema } from "@ghrah/protocol";
import { flushPromises, mount } from "@vue/test-utils";
import { createPinia, setActivePinia } from "pinia";
import { beforeEach, expect, it, vi } from "vitest";
import WaitRow from "./wait-row.vue";
import type { ReplyType, WaitRequest } from "./wait-request.js";

const request: WaitRequest = {
  requestId: "approval",
  turnId: "turn",
  agentId: "agent",
  prompt: "Review changes.",
  reply: { kind: "boolean" },
  context: { abilityName: "review", toolCall: { path: "report.md\nmore" } },
  deadline: null,
  status: "pending",
};

beforeEach(() => setActivePinia(createPinia()));

const hints: [ReplyType, string][] = [
  [{ kind: "boolean" }, "Needs: approve or reject"],
  [{ kind: "text" }, "Needs: a text answer"],
  [{ kind: "choice", maxSelections: 1, options: [] }, "Needs: pick one"],
  [{ kind: "choice", maxSelections: 3, options: [] }, "Needs: pick up to 3"],
  [{ kind: "choice", maxSelections: Infinity, options: [] }, "Needs: pick one or more"],
  [{ kind: "object", fields: [] }, "Needs: fill in 0 fields"],
];

it.each(hints)("shows a read-only hint for %j", (reply, text) => {
  const wrapper = mount(WaitRow, { props: { request: { ...request, reply } } });
  expect(wrapper.attributes("data-reply-kind")).toBe(reply.kind);
  expect(wrapper.get(".tc-wait-reply-hint").text()).toBe(text);
  expect(wrapper.text()).toContain("review: report.md");
  expect(wrapper.find("input, select, textarea, button[type=submit]").exists()).toBe(false);
  expect(wrapper.findAll("button")).toHaveLength(1);
  wrapper.unmount();
});

it("opens the inbox and highlights the existing request", async () => {
  const hitl = useHitlStore();
  hitl.onHitlRequest(
    HITLRequestPayloadSchema.parse({
      promise_id: request.requestId,
      project_id: "project",
      agent_name: "Coder",
      agent_id: request.agentId,
      ability_name: "review",
    }),
  );
  const toggle = document.createElement("button");
  toggle.setAttribute("aria-controls", "hitl-bar-panel");
  toggle.setAttribute("aria-expanded", "false");
  const panel = document.createElement("div");
  panel.id = "hitl-bar-panel";
  panel.scrollIntoView = vi.fn();
  toggle.onclick = () => {
    document.body.append(panel);
  };
  document.body.append(toggle);
  const wrapper = mount(WaitRow, { props: { request } });
  try {
    await wrapper.get(".tc-wait-jump").trigger("click");
    await flushPromises();
    expect([...hitl.selectedIds]).toEqual([request.requestId]);
    expect(panel.scrollIntoView).toHaveBeenCalledWith({ block: "nearest" });
  } finally {
    wrapper.unmount();
    toggle.remove();
    panel.remove();
  }
});
