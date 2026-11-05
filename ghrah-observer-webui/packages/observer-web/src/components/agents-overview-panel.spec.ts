// SPDX-FileCopyrightText: 2026 chenxya <chenxya@ghrah.org>
//
// SPDX-License-Identifier: Apache-2.0

// @vitest-environment happy-dom

import {
  useAgentsStore,
  useBranchesStore,
  useContextUsageStore,
  useProjectsStore,
  useRoomsStore,
  useSessionsStore,
} from "@ghrah/observer-core";
import { mount } from "@vue/test-utils";
import { createPinia, setActivePinia } from "pinia";
import { beforeEach, describe, expect, it } from "vitest";
import AgentsOverviewPanel from "./agents-overview-panel.vue";

describe("AgentsOverviewPanel", () => {
  beforeEach(() => {
    setActivePinia(createPinia());
    useProjectsStore().setProjectsFromList([
      {
        project_id: "p1",
        name: "Project One",
        status: "active",
        archived_at: null,
        agents: [
          { name: "Coder", agent_id: "a1", cluster_id: "c1", runtime_status: "running" },
          { name: "Reviewer", agent_id: "a2", cluster_id: "c2", runtime_status: "stopped" },
        ],
      } as never,
      {
        project_id: "p2",
        name: "Project Two",
        status: "active",
        archived_at: null,
        agents: [{ name: "Foreign", agent_id: "a9" }],
      } as never,
    ]);
    useAgentsStore().replaceProjectAgents(
      "p1",
      [
        { project_id: "p1", agent_id: "a1", name: "Coder", runtime_state: "running" },
        { project_id: "p1", agent_id: "a2", name: "Reviewer", runtime_state: "stopped" },
      ],
      { status: "active", archived_at: null },
    );
    useRoomsStore().replaceProjectRooms(
      "p1",
      [
        {
          room_id: "r1",
          project_id: "p1",
          name: "Architecture",
          status: "active",
          members: [
            {
              subject: "a1",
              subject_type: "agent",
              subject_name: "Coder",
              joined_at: "",
            },
          ],
          seq_watermark: 0,
          version: 1,
          created_at: "",
          updated_at: "",
          archived_at: null,
        },
      ],
      "active",
    );
    const agent = { projectId: "p1", agentId: "a1", agentName: "Coder" };
    const sessionsStore = useSessionsStore();
    sessionsStore.replaceAgentSessions(agent, [{ session_id: "s1", agent_name: "Coder" } as never]);
    sessionsStore.setActiveSession(agent, "s1");
    useBranchesStore().replaceSessionBranches({ ...agent, sessionId: "s1" }, [
      { branch_id: "b1", session_id: "s1", name: "main" } as never,
    ]);
    useBranchesStore().setActiveBranch({ ...agent, sessionId: "s1" }, "b1");
  });

  it("renders only project agents with room and runtime context", () => {
    const wrapper = mount(AgentsOverviewPanel, { props: { projectId: "p1" } });

    expect(wrapper.text()).toContain("Coder");
    expect(wrapper.text()).toContain("Reviewer");
    expect(wrapper.text()).toContain("Architecture");
    expect(wrapper.text()).not.toContain("Foreign");
    expect(wrapper.findAll("li")[1].find("button").attributes("disabled")).toBeDefined();
  });

  it("selects Session and Branch explicitly before opening ActionChain", async () => {
    const wrapper = mount(AgentsOverviewPanel, { props: { projectId: "p1" } });
    await wrapper.findAll("li")[0].find("button").trigger("click");

    const target = {
      projectId: "p1",
      agentId: "a1",
      agentName: "Coder",
      sessionId: "s1",
      branchId: "b1",
    };
    expect(wrapper.emitted("openAgent")?.[0]).toEqual([target]);
    expect(useAgentsStore().selectedAgentTarget).toEqual({
      projectId: "p1",
      agentId: "a1",
      agentName: "Coder",
    });
    expect(useSessionsStore().activeSessionId(target)).toBe("s1");
    expect(useBranchesStore().activeBranchId(target)).toBe("b1");
  });

  it("falls back to an explicit label for unknown runtime states", () => {
    const projects = useProjectsStore();
    const current = projects.projects.get("p1");
    projects.onProjectEvent({
      project: {
        ...current,
        agents: [
          ...(current?.agents ?? []),
          { name: "Future Agent", agent_id: "a3", runtime_status: "recovering" },
        ],
      },
    } as never);

    const wrapper = mount(AgentsOverviewPanel, { props: { projectId: "p1" } });

    expect(wrapper.text()).toContain("Unknown");
    expect(wrapper.text()).not.toContain("agentsOverview.status.recovering");
  });

  it("renders ratio usage bar for budgeted agents and cumulative for unbudgeted", () => {
    const usage = useContextUsageStore();
    usage.onContextUsageUpdated(
      {
        project_id: "p1",
        agent_id: "a1",
        cluster_id: "c1",
        agent_name: "Coder",
        phase: "post_call",
        occupied_tokens: 4096,
        basis: "real",
        budget_tokens: 8192,
        compact_threshold: 0.8,
        real_input_tokens: 4096,
        real_output_tokens: 200,
        compaction: null,
        iteration: 2,
      },
      111,
    );
    usage.onContextUsageUpdated(
      {
        project_id: "p1",
        agent_id: "a2",
        cluster_id: "c2",
        agent_name: "Reviewer",
        phase: "post_call",
        occupied_tokens: null,
        basis: "real",
        budget_tokens: 0,
        compact_threshold: null,
        real_input_tokens: 1234,
        real_output_tokens: 100,
        compaction: null,
        iteration: 1,
      },
      112,
    );

    const wrapper = mount(AgentsOverviewPanel, { props: { projectId: "p1" } });

    const ratioRow = wrapper.findAll("li")[0];
    expect(ratioRow.find(".agents-overview-usage-bar").exists()).toBe(true);
    expect(ratioRow.find(".agents-overview-usage-fill").attributes("style")).toContain("50%");
    expect(ratioRow.text()).toContain("4096 / 8192 (50%)");

    const cumulativeRow = wrapper.findAll("li")[1];
    expect(cumulativeRow.find(".agents-overview-usage-bar").exists()).toBe(false);
    expect(cumulativeRow.text()).toContain("1234 tokens used");
    expect(cumulativeRow.text()).toContain("No window configured");
    expect(cumulativeRow.find(".agents-overview-usage-breakdown").exists()).toBe(true);
    expect(cumulativeRow.text()).toContain("in 1234");
    expect(cumulativeRow.text()).toContain("out 100");
    expect(cumulativeRow.text()).toContain("cached 0");
  });

  it("renders cache breakdown and budget source tag for budgeted agents", () => {
    const usage = useContextUsageStore();
    usage.onContextUsageUpdated(
      {
        project_id: "p1",
        agent_id: "a1",
        cluster_id: "c1",
        agent_name: "Coder",
        phase: "post_call",
        occupied_tokens: 4096,
        basis: "real",
        budget_tokens: 8192,
        budget_source: "vendor",
        compact_threshold: 0.8,
        real_input_tokens: 4096,
        real_output_tokens: 200,
        real_cache_read_tokens: 3200,
        real_cache_write_tokens: 128,
        compaction: null,
        iteration: 2,
      },
      111,
    );

    const wrapper = mount(AgentsOverviewPanel, { props: { projectId: "p1" } });

    const row = wrapper.findAll("li")[0];
    expect(row.find(".agents-overview-usage-breakdown").exists()).toBe(true);
    expect(row.text()).toContain("in 4096");
    expect(row.text()).toContain("out 200");
    expect(row.text()).toContain("cached 3200");
    expect(row.text()).toContain("vendor");
  });
});
