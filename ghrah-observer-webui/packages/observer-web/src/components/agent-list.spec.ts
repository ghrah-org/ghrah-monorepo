// SPDX-FileCopyrightText: 2026 chenxya <chenxya@ghrah.org>
//
// SPDX-License-Identifier: Apache-2.0

// @vitest-environment happy-dom

import {
  useAgentsStore,
  useContextUsageStore,
  useProjectsStore,
  useRoomsStore,
} from "@ghrah/observer-core";
import type {
  AgentSpawnedPayload,
  ContextUsageUpdatedPayload,
  RoomInfoPayload,
} from "@ghrah/protocol";
import { mount } from "@vue/test-utils";
import { createPinia, setActivePinia } from "pinia";
import { beforeEach, describe, expect, it, vi } from "vitest";

// 隔离 AgentActionMenu（与本组件渲染无关的动作浮层）
vi.mock("@/components/agent-action-menu.vue", () => ({
  default: { template: "<div />" },
}));

import AgentList from "./agent-list.vue";

function spawn(name: string): AgentSpawnedPayload {
  return {
    type: "agent_spawned",
    project_id: "p1",
    agent_id: `${name}-id`,
    cluster_id: "cluster",
    incarnation_id: "incarnation",
    recovery_mode: "fresh",
    name,
    config: { name, agent_id: `${name}-id` },
  } as unknown as AgentSpawnedPayload;
}

function room(id: string, name: string, members: string[] = []): RoomInfoPayload {
  return {
    room_id: id,
    project_id: "p1",
    name,
    status: "active",
    members: members.map((s) => ({
      subject: s,
      subject_type: "agent" as const,
      subject_name: s,
      joined_at: "",
    })),
    seq_watermark: 0,
    version: 1,
    created_at: "",
    updated_at: "",
    archived_at: null,
  };
}

function usagePayload(
  overrides: Partial<ContextUsageUpdatedPayload> = {},
): ContextUsageUpdatedPayload {
  return {
    project_id: "p1",
    agent_id: "architect-id",
    cluster_id: "cluster",
    agent_name: "architect",
    phase: "post_call",
    occupied_tokens: 4096,
    basis: "real",
    budget_tokens: 8192,
    budget_source: "declared",
    compact_threshold: 0.8,
    real_input_tokens: 4096,
    real_output_tokens: 200,
    real_cache_read_tokens: 3200,
    real_cache_write_tokens: 128,
    compaction: null,
    iteration: 3,
    ...overrides,
  };
}

function agentItem(wrapper: ReturnType<typeof mount>, name: string) {
  return wrapper.findAll("li").find((li) => li.text().includes(name));
}

function mountAgentList() {
  return mount(AgentList, {
    global: { stubs: { AgentActionMenu: true } },
  });
}

describe("AgentList", () => {
  beforeEach(() => {
    setActivePinia(createPinia());
    useProjectsStore().setActiveProject("p1");
  });

  it("shows empty state when no agents", () => {
    const wrapper = mountAgentList();
    expect(wrapper.text()).toContain("No active agents");
  });

  it("opens Agent settings from Spawn", async () => {
    const wrapper = mountAgentList();
    await wrapper.get(".ui-button-primary").trigger("click");
    expect(wrapper.emitted("openSettings")?.[0]).toEqual(["agents"]);
  });

  it("click selects agent (drives per-agent ActionChain)", async () => {
    const agents = useAgentsStore();
    agents.onAgentSpawned(spawn("architect"));
    agents.onAgentSpawned(spawn("frontend"));
    const wrapper = mountAgentList();
    await wrapper.vm.$nextTick();
    await agentItem(wrapper, "frontend")!.trigger("click");
    expect(agents.selectedAgentName).toBe("frontend");
  });

  it("shows room membership badges per agent (multi-room → multiple badges)", async () => {
    const agents = useAgentsStore();
    agents.onAgentSpawned(spawn("architect"));
    agents.onAgentSpawned(spawn("tester"));
    const rooms = useRoomsStore();
    // architect 在 3 个 room；tester 不在任何 room
    rooms.replaceProjectRooms(
      "p1",
      [
        room("r1", "arch", ["architect"]),
        room("r2", "frontend", ["architect"]),
        room("r3", "backend", ["architect"]),
      ],
      "active",
    );
    const wrapper = mountAgentList();
    await wrapper.vm.$nextTick();
    const architectBadges = agentItem(wrapper, "architect")!.findAll(".agent-room-badge");
    expect(architectBadges).toHaveLength(3);
    expect(architectBadges.map((b) => b.attributes("title")).sort()).toEqual([
      "arch",
      "backend",
      "frontend",
    ]);
    expect(agentItem(wrapper, "tester")!.findAll(".agent-room-badge")).toHaveLength(0);
  });

  it("matches room badges by stable agent ID", async () => {
    const agents = useAgentsStore();
    agents.onAgentSpawned({
      ...spawn("shuoxi"),
      agent_id: "stable-shuoxi",
    } as AgentSpawnedPayload);
    const rooms = useRoomsStore();
    const stableRoom = room("r1", "chat");
    stableRoom.members = [
      {
        subject: "stable-shuoxi",
        subject_type: "agent",
        subject_name: "shuoxi",
        joined_at: "",
      },
    ];
    rooms.replaceProjectRooms("p1", [stableRoom], "active");

    const wrapper = mountAgentList();
    await wrapper.vm.$nextTick();
    expect(agentItem(wrapper, "shuoxi")!.findAll(".agent-room-badge")).toHaveLength(1);
  });

  it("shows usage ring with hover title for agents with usage data", async () => {
    const agents = useAgentsStore();
    agents.onAgentSpawned(spawn("architect"));
    agents.onAgentSpawned(spawn("tester"));
    useContextUsageStore().onContextUsageUpdated(usagePayload(), 111);
    const wrapper = mountAgentList();
    await wrapper.vm.$nextTick();
    const architectRing = agentItem(wrapper, "architect")!.find("svg");
    expect(architectRing.exists()).toBe(true);
    expect(agentItem(wrapper, "tester")!.find("svg").exists()).toBe(false);
    // hover title 含三段明细（输入含缓存命中）
    const title = agentItem(wrapper, "architect")!.find(".inline-flex").attributes("title");
    expect(title).toContain("Context: 4096 / 8192 tokens (50%)");
    expect(title).toContain("cache hit 3200");
  });

  it("usage ring reflects ratio in stroke-dashoffset", async () => {
    const agents = useAgentsStore();
    agents.onAgentSpawned(spawn("architect"));
    const usage = useContextUsageStore();
    usage.onContextUsageUpdated(usagePayload({ occupied_tokens: 4096 }), 111);
    const wrapper = mountAgentList();
    await wrapper.vm.$nextTick();
    const circles = agentItem(wrapper, "architect")!.findAll("circle");
    const progress = circles.at(1);
    expect(progress).toBeDefined();
    const offset = Number(progress!.attributes("stroke-dashoffset"));
    // 4096/8192 = 50%：offset 应为周长一半
    const circumference = 2 * Math.PI * 5;
    expect(offset).toBeCloseTo(circumference * 0.5, 1);
  });

  it("cumulative ring title includes last-call breakdown without window", async () => {
    const agents = useAgentsStore();
    agents.onAgentSpawned(spawn("architect"));
    const usage = useContextUsageStore();
    usage.onContextUsageUpdated(
      usagePayload({ budget_tokens: 0, budget_source: null, occupied_tokens: null }),
      111,
    );
    const wrapper = mountAgentList();
    await wrapper.vm.$nextTick();
    const title = agentItem(wrapper, "architect")!.find(".inline-flex").attributes("title");
    expect(title).toContain("4096 tokens used");
    expect(title).toContain("Last call: in 4096 · out 200 · cached 3200");
  });
});
