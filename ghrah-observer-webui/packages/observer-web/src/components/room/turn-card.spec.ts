// SPDX-FileCopyrightText: 2026 chenxya <chenxya@ghrah.org>
//
// SPDX-License-Identifier: Apache-2.0

// @vitest-environment happy-dom

import { ActionNodeSchema } from "@ghrah/protocol";
import { enableAutoUnmount, mount } from "@vue/test-utils";
import { afterEach, beforeEach, describe, expect, it } from "vitest";

import { delivery, entry, turn } from "@/test/turn-card-fixtures";
import TurnCard from "./turn-card.vue";

enableAutoUnmount(afterEach);
beforeEach(() => sessionStorage.clear());

const roomId = "room-1";
const storageKey = "tc-expanded:room-1:turn-1";
const completed = turn({ status: "closed", closed_at: "2026-11-26T02:00:06Z" });
const nodes = [
  ActionNodeSchema.parse({ id: "node-1", ability_names: ["read_file"] }),
  ActionNodeSchema.parse({ id: "node-2", ability_names: ["write_file"] }),
];

describe("TurnCard", () => {
  it("keeps the body hidden, exposes a cached summary, and lays out cached steps on demand", async () => {
    const wrapper = mount(TurnCard, {
      props: {
        roomId,
        turn: completed,
        agentDisplayName: "Coder",
        opener: entry(),
        detail: { turn: completed, nodes },
      },
    });
    expect(wrapper.attributes("data-expanded")).toBe("false");
    expect(wrapper.get(".tc-main .tc-trigger").text()).toBe("↳ reply to Ada");
    expect(wrapper.get(".tc-name").text()).toBe("Coder");
    expect(wrapper.get(".tc-avatar").text()).toBe("C");
    expect(wrapper.get(".tc-last").text()).toBe("write_file");
    expect(wrapper.find(".tc-foot").exists()).toBe(false);

    const toggle = wrapper.get(".tc-toggle");
    const body = wrapper.get(".tc-body");
    expect(toggle.attributes("aria-controls")).toBe(body.attributes("id"));
    expect(body.element.hasAttribute("hidden")).toBe(true);
    await toggle.trigger("click");
    expect(toggle.attributes("aria-expanded")).toBe("true");
    expect(body.element.hasAttribute("hidden")).toBe(false);
    expect(wrapper.findAll(".node-title").map((node) => node.text())).toEqual([
      "read_file",
      "write_file",
    ]);
    expect(wrapper.get(".tc-foot").attributes("aria-label")).toBe(
      "Turn 1 · 2 steps · 6s · in 1.4k / out 200 tok · cache 50%",
    );
    expect(wrapper.emitted("expand")).toBeUndefined();
  });

  it("requests detail on expansion and shows loading, retry and a resolved empty state", async () => {
    const failed = turn({ status: "failed", abort_reason: "invalid arguments" });
    const wrapper = mount(TurnCard, { props: { roomId, turn: failed } });
    expect(wrapper.emitted("expand")).toBeUndefined();
    await wrapper.get(".tc-toggle").trigger("click");
    expect(wrapper.emitted("expand")).toEqual([[failed]]);
    expect(wrapper.get(".tc-skeleton").text()).toBe("Loading steps…");

    await wrapper.setProps({ error: "request failed" });
    expect(wrapper.get(".tc-error [role=alert]").text()).toBe("Failed to load steps");
    await wrapper.get(".tc-error button").trigger("click");
    expect(wrapper.emitted("expand")).toEqual([[failed], [failed]]);
    await wrapper.setProps({ error: null, detail: { turn: failed, nodes: [] } });
    expect(wrapper.get(".tc-empty").text()).toBe("No steps recorded");
    expect(wrapper.get(".tc-phase-text").text()).toBe("Failed · invalid arguments");
    expect(wrapper.find(".tc-skeleton").exists()).toBe(false);
  });

  it("restores the expansion choice for the same room and turn", async () => {
    const wrapper = mount(TurnCard, { props: { roomId, turn: completed } });
    await wrapper.get(".tc-toggle").trigger("click");
    expect(sessionStorage.getItem(storageKey)).toBe("true");
    const restored = mount(TurnCard, { props: { roomId, turn: completed } });
    expect(restored.attributes("data-expanded")).toBe("true");
    expect(restored.emitted("expand")).toEqual([[completed]]);
    await restored.setProps({ roomId: "room-2" });
    expect(restored.attributes("data-expanded")).toBe("false");
  });

  it("opens a waiting turn by default and respects an explicit collapse", async () => {
    const waiting = turn({ status: "waiting_hitl" });
    const wrapper = mount(TurnCard, {
      props: { roomId, turn: waiting },
      slots: { waits: "Pending approval" },
    });
    expect(wrapper.attributes("data-expanded")).toBe("true");
    expect(wrapper.get(".tc-phase-text").text()).toBe("Waiting for approval");
    expect(wrapper.get(".tc-wait-list").text()).toBe("Pending approval");
    expect(wrapper.emitted("expand")).toEqual([[waiting]]);
    await wrapper.get(".tc-toggle").trigger("click");
    expect(sessionStorage.getItem(storageKey)).toBe("false");
    const restored = mount(TurnCard, { props: { roomId, turn: waiting } });
    expect(restored.attributes("data-expanded")).toBe("false");
    expect(restored.emitted("expand")).toBeUndefined();
  });

  it("shows no stats on an open turn and separates consumed and waiting inserts", async () => {
    const open = turn();
    const wrapper = mount(TurnCard, {
      props: {
        roomId,
        turn: open,
        detail: { turn: open, nodes },
        entries: [
          entry({ deliveries: [delivery({ display_state: "consumed", injection_step: 2 })] }),
          entry({ entry_id: "waiting", deliveries: [delivery()] }),
        ],
      },
    });
    expect(wrapper.get(".tc-injected").text()).toBe("1 injected · 1 pending");
    await wrapper.get(".tc-toggle").trigger("click");
    expect(wrapper.find(".tc-foot").exists()).toBe(false);
    expect(wrapper.get(".insert-note--inline").text()).toBe(
      "↶ injected · step 2 · Check the logs.",
    );
    expect(wrapper.get(".insert-waiting").text()).toBe("Waiting to be consumed");
  });

  it("shows the compact badge and token change on a compact-only turn", async () => {
    const compact = ActionNodeSchema.parse({
      id: "compact",
      is_snapshot: true,
      metadata: { tokens_before: 1200, tokens_after: 300, trigger_source: "manual" },
    });
    const wrapper = mount(TurnCard, {
      props: { roomId, turn: completed, detail: { turn: completed, nodes: [compact] } },
    });
    expect(wrapper.classes()).toContain("turn-card--compact");
    expect(wrapper.get(".tc-badge--compact").text()).toBe("⇕ compact · manual");
    expect(wrapper.get(".tc-last").text()).toBe("1200 → 300 tok");
    await wrapper.get(".tc-toggle").trigger("click");
    expect(wrapper.find(".tc-last").exists()).toBe(false);
    expect(wrapper.find(".tc-timeline").exists()).toBe(true);
  });

  it.each([
    ["aborted", "Aborted · stopped"],
    ["rolled_back", "Rolled back · stopped"],
  ])("shows the recovery branch for %s turns", async (status, expected) => {
    const terminal = turn({ status, abort_reason: "stopped", recovery_branch_id: "branch-1" });
    const wrapper = mount(TurnCard, {
      props: { roomId, turn: terminal, detail: { turn: terminal, nodes } },
    });
    expect(wrapper.attributes("data-status")).toBe(status);
    expect(wrapper.get(".tc-phase-text").text()).toBe(expected);
    await wrapper.get(".tc-toggle").trigger("click");
    expect(wrapper.get(".tc-recovery code").text()).toBe("branch-1");
  });

  it("shows the incomplete node diagnostic for an interrupted turn", async () => {
    const interrupted = turn({ status: "interrupted" });
    const diagnostic = ActionNodeSchema.parse({
      id: "node-3",
      metadata: { node_kind: "incomplete", error: "Connection lost" },
    });
    const wrapper = mount(TurnCard, {
      props: {
        roomId,
        turn: interrupted,
        detail: { turn: interrupted, nodes: [...nodes, diagnostic] },
      },
    });
    await wrapper.get(".tc-toggle").trigger("click");
    expect(wrapper.attributes("data-status")).toBe("interrupted");
    expect(wrapper.get(".node-error").text()).toBe("Connection lost");
  });
});
