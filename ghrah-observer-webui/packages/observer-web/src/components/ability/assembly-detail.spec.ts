// SPDX-FileCopyrightText: 2026 chenxya <chenxya@ghrah.org>
//
// SPDX-License-Identifier: Apache-2.0

// @vitest-environment happy-dom

import {
  HttpError,
  setAbilityControlClientFactory,
  setAgentControlClientFactory,
  useConnectionStore,
  useProjectsStore,
} from "@ghrah/observer-core";
import {
  AbilityAdminViewSchema,
  AbilityApiStatusSchema,
  AbilityChangesPageSchema,
  AbilityTreeViewSchema,
  AgentAdminViewSchema,
  AgentDeletedViewSchema,
} from "@ghrah/protocol";
import { flushPromises, mount } from "@vue/test-utils";
import { createPinia, setActivePinia } from "pinia";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import AssemblyDetail from "./assembly-detail.vue";

const abortTurn = vi.fn();

vi.mock("@/composables/useObserver", () => ({
  useObserver: () => ({ abortTurn, error: { value: null } }),
}));

const PROJECT_ID = "p1";
const AGENT_ID = "a1";

const status = AbilityApiStatusSchema.parse({
  ability_source: "builtin",
  features: {
    tree: true,
    grants_write: true,
    versions_read: false,
    versions_write: false,
    packages_read: false,
    packages_install: false,
    surface_write: false,
  },
});

const adminView = AbilityAdminViewSchema.parse({
  project_id: PROJECT_ID,
  agent_id: AGENT_ID,
  revision: 1,
  lifecycle: { state: "running", requires_respawn: false, pending_reasons: [] },
  grants: [],
  surface: { current_runtime: [], pending_respawn: { added: [], removed: [] } },
});

const treeView = AbilityTreeViewSchema.parse({
  project_id: PROJECT_ID,
  agent_id: AGENT_ID,
  run_id: "run-1",
  revision: 1,
  abilities: [],
  bound_tools: [],
});

interface AgentViewOptions {
  desired?: "running" | "stopped";
  availability?: "online" | "offline" | "unknown";
  busy?: boolean | null;
  version?: number;
}

function agentView(options: AgentViewOptions = {}) {
  const desired = options.desired ?? "running";
  const availability = options.availability ?? "online";
  return AgentAdminViewSchema.parse({
    project_id: PROJECT_ID,
    specification: {
      agent_id: AGENT_ID,
      name: "Coder",
      presentation: {},
      desired_state: desired,
      runtime_status: availability === "online" ? "running" : "stopped",
      project_version: options.version ?? 5,
    },
    runtime: {
      availability,
      incarnation_id: availability === "online" ? "i1" : null,
      busy: options.busy ?? false,
    },
  });
}

function stoppedView() {
  return agentView({ desired: "stopped", availability: "offline" });
}

function setProject() {
  useProjectsStore().setProjectsFromList([
    {
      project_id: PROJECT_ID,
      name: "Project One",
      archived_at: null,
      version: 5,
      agents: [
        {
          agent_id: AGENT_ID,
          name: "Coder",
          desired_state: "running",
          runtime_status: "running",
        },
      ],
    } as never,
  ]);
}

function setAbilityClient() {
  setAbilityControlClientFactory(
    () =>
      ({
        status: async () => status,
        admin: async () => adminView,
        tree: async () => treeView,
        changes: async () =>
          AbilityChangesPageSchema.parse({ changes: [], next_after_seq: 0, has_more: false }),
      }) as never,
  );
}

function setAgentClient(overrides: Record<string, unknown> = {}) {
  const client = {
    get: vi.fn(async () => agentView()),
    setDesiredState: vi.fn(async (_agentId: string, state: "running" | "stopped") =>
      state === "running" ? agentView() : stoppedView(),
    ),
    delete: vi.fn(async () =>
      AgentDeletedViewSchema.parse({
        project_id: PROJECT_ID,
        agent_id: AGENT_ID,
        project_version: 6,
      }),
    ),
    ...overrides,
  };
  setAgentControlClientFactory(() => client as never);
  return client;
}

async function mountDetail() {
  const wrapper = mount(AssemblyDetail, {
    props: { projectId: PROJECT_ID, agentId: AGENT_ID },
  });
  await flushPromises();
  return wrapper;
}

function buttonByText(wrapper: ReturnType<typeof mount>, text: string) {
  return wrapper.findAll("button").filter((button) => button.text() === text);
}

describe("AssemblyDetail lifecycle actions", () => {
  beforeEach(() => {
    setActivePinia(createPinia());
    useConnectionStore().setConnected();
    setProject();
    setAbilityClient();
    abortTurn.mockReset();
    abortTurn.mockResolvedValue({ success: true, data: { aborted: true } });
  });

  afterEach(() => {
    setAbilityControlClientFactory(null);
    setAgentControlClientFactory(null);
  });

  it("shows the agent identity and a stop action while it is online", async () => {
    setAgentClient();
    const wrapper = await mountDetail();

    const identity = wrapper.get(".assembly-agent-identity").text();
    expect(identity).toContain("Coder");
    expect(identity).toContain(AGENT_ID);
    expect(identity).toContain("Running requested");
    expect(identity).toContain("Online");
    expect(wrapper.find(".assembly-agent-stop").exists()).toBe(true);
    expect(wrapper.find(".assembly-agent-start").exists()).toBe(false);
    wrapper.unmount();
  });

  it("turns into a start action once the agent is stopped", async () => {
    setAgentClient({ get: vi.fn(async () => stoppedView()) });
    const wrapper = await mountDetail();

    expect(wrapper.find(".assembly-agent-stop").exists()).toBe(false);
    expect(wrapper.find(".assembly-agent-start").exists()).toBe(true);
    expect(wrapper.get(".assembly-agent-identity").text()).toContain("Stopped requested");
    wrapper.unmount();
  });

  it("asks before stopping and declares the desired state", async () => {
    const client = setAgentClient({
      // 动作后的补读必须读到停止态，否则条上会回跳成在线（服务端此时已是停止态）。
      get: vi.fn().mockResolvedValueOnce(agentView()).mockResolvedValue(stoppedView()),
    });
    const wrapper = await mountDetail();

    await wrapper.get(".assembly-agent-stop").trigger("click");
    expect(wrapper.text()).toContain("Stop Coder?");
    const confirm = buttonByText(wrapper, "Stop").at(-1);
    expect(confirm).toBeDefined();
    await confirm!.trigger("click");
    await flushPromises();

    expect(client.setDesiredState).toHaveBeenCalledWith(AGENT_ID, "stopped", 5);
    expect(wrapper.text()).toContain("Stopped; desired state is stopped.");
    expect(wrapper.find(".assembly-agent-start").exists()).toBe(true);
    wrapper.unmount();
  });

  it("starts an offline agent without a confirmation", async () => {
    const client = setAgentClient({ get: vi.fn(async () => stoppedView()) });
    const wrapper = await mountDetail();

    await wrapper.get(".assembly-agent-start").trigger("click");
    await flushPromises();

    expect(client.setDesiredState).toHaveBeenCalledWith(AGENT_ID, "running", 5);
    expect(wrapper.text()).toContain("Started; assembling.");
    wrapper.unmount();
  });

  it("blocks deletion while the agent is processing messages", async () => {
    setAgentClient({
      get: vi.fn(async () => agentView({ busy: true })),
    });
    const wrapper = await mountDetail();

    expect(wrapper.text()).toContain("Processing");
    const remove = wrapper.get(".assembly-agent-delete");
    expect(remove.attributes("disabled")).toBeDefined();
    wrapper.unmount();
  });

  it("picks up the processing state without a manual refresh", async () => {
    vi.useFakeTimers();
    try {
      let view = agentView({ busy: false });
      setAgentClient({ get: vi.fn(async () => view) });
      const wrapper = await mountDetail();
      expect(wrapper.find(".assembly-agent-busy").exists()).toBe(false);

      view = agentView({ busy: true });
      vi.advanceTimersByTime(2000);
      await flushPromises();
      expect(wrapper.find(".assembly-agent-busy").exists()).toBe(true);
      expect(wrapper.get(".assembly-agent-delete").attributes("disabled")).toBeDefined();

      view = agentView({ busy: false });
      vi.advanceTimersByTime(2000);
      await flushPromises();
      expect(wrapper.find(".assembly-agent-busy").exists()).toBe(false);
      expect(wrapper.get(".assembly-agent-delete").attributes("disabled")).toBeUndefined();
      wrapper.unmount();
    } finally {
      vi.useRealTimers();
    }
  });

  it("deletes after a strong confirmation and emits the deleted event", async () => {
    const client = setAgentClient();
    const wrapper = await mountDetail();

    await wrapper.get(".assembly-agent-delete").trigger("click");
    expect(wrapper.text()).toContain("Delete Coder?");
    await buttonByText(wrapper, "Delete").at(-1)!.trigger("click");
    await flushPromises();

    expect(client.delete).toHaveBeenCalledWith(AGENT_ID, 5);
    expect(wrapper.emitted("deleted")).toHaveLength(1);
    expect(wrapper.text()).toContain("Agent deleted; history kept.");
    wrapper.unmount();
  });

  it("maps a busy deletion rejection to an actionable message", async () => {
    setAgentClient({
      delete: vi.fn(async () => {
        throw new HttpError({ code: "agent_busy", status: 409, message: "agent is busy" });
      }),
    });
    const wrapper = await mountDetail();

    await wrapper.get(".assembly-agent-delete").trigger("click");
    await buttonByText(wrapper, "Delete").at(-1)!.trigger("click");
    await flushPromises();

    expect(wrapper.text()).toContain("The agent is processing messages. Try again later.");
    expect(wrapper.emitted("deleted")).toBeUndefined();
    wrapper.unmount();
  });

  it("maps a version conflict on stop to a refresh hint", async () => {
    setAgentClient({
      setDesiredState: vi.fn(async () => {
        throw new HttpError({ code: "conflict", status: 409, message: "version changed" });
      }),
    });
    const wrapper = await mountDetail();

    await wrapper.get(".assembly-agent-stop").trigger("click");
    await buttonByText(wrapper, "Stop").at(-1)!.trigger("click");
    await flushPromises();

    expect(wrapper.text()).toContain("The project changed. Refresh and try again.");
    wrapper.unmount();
  });

  it("aborts the current turn and reports the outcome", async () => {
    const client = setAgentClient();
    const wrapper = await mountDetail();
    const readsBefore = client.get.mock.calls.length;

    await wrapper.get(".assembly-agent-abort").trigger("click");
    await flushPromises();
    // nudgeControl 的首个补读是 0ms setTimeout：等一拍宏任务再断言。
    await new Promise((resolve) => setTimeout(resolve, 10));
    await flushPromises();
    expect(abortTurn).toHaveBeenCalledWith({
      projectId: PROJECT_ID,
      agentId: AGENT_ID,
      agentName: "Coder",
    });
    // 中断只改轮次与运行态、不改项目版本：必须主动补读一次，条上状态才会更新。
    expect(client.get.mock.calls.length).toBeGreaterThan(readsBefore);
    expect(wrapper.text()).toContain("Current turn aborted.");

    abortTurn.mockResolvedValue({ success: false, error: "turn_not_found" });
    await wrapper.get(".assembly-agent-abort").trigger("click");
    await flushPromises();
    expect(wrapper.text()).toContain("No turn is in progress.");
    wrapper.unmount();
  });

  it("shows the runtime read failure without breaking the bar", async () => {
    setAgentClient({
      get: vi.fn(async () => {
        throw new HttpError({ code: "capability_unavailable", status: 503, message: "disabled" });
      }),
    });
    const wrapper = await mountDetail();

    expect(wrapper.text()).toContain("The observer control plane is disabled.");
    expect(wrapper.get(".assembly-agent-identity").text()).toContain("Coder");
    expect(wrapper.get(".assembly-agent-identity").text()).toContain("Runtime unavailable");
    wrapper.unmount();
  });
});
