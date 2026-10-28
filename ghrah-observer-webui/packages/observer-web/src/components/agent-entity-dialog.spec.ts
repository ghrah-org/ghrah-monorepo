// @vitest-environment happy-dom
import { useConnectionStore, useProjectsStore } from "@ghrah/observer-core";
import { flushPromises, mount } from "@vue/test-utils";
import { createPinia, setActivePinia } from "pinia";
import { beforeEach, expect, it, vi } from "vitest";
import { ref } from "vue";
import AgentEntityDialog from "./agent-entity-dialog.vue";
const api = vi.hoisted(() => ({
  snapshot: vi.fn(),
  start: vi.fn(),
  stop: vi.fn(),
  listSessions: vi.fn(),
  getHistory: vi.fn(),
  createSession: vi.fn(),
  activateSession: vi.fn(),
  listInbox: vi.fn(),
  listOutbox: vi.fn(),
  retry: vi.fn(),
  resume: vi.fn(),
  configure: vi.fn(),
  create: vi.fn(),
  list: vi.fn(),
}));
vi.mock("@/composables/useObserver", () => ({
  useObserver: () => ({
    client: ref({
      agent: () => ({ ...api, context: api, mailbox: api, abilities: api }),
      createAgentEntity: api.create,
      listAgentEntities: api.list,
    }),
  }),
}));
function snapshot(online = false) {
  return {
    identity: { project_id: "p1", agent_id: "a1" },
    specification: {
      name: "Designer",
      cluster_id: "c1",
      desired_state: online ? "running" : "stopped",
      abilities: ["send_message"],
      manifest_ref: "",
      runtime_error: "",
    },
    specification_revision: 5,
    runtime: {
      availability: online ? "online" : "offline",
      incarnation_id: online ? "instance-1" : null,
      busy: false,
    },
    context: {},
    mailbox: {},
    abilities: {
      configured_types: ["send_message"],
      loaded_names: online ? ["send_message"] : null,
      available_types: ["send_message", "conversation"],
    },
    observed_at: "now",
  };
}
function render(agentId: string | undefined = "a1") {
  return mount(AgentEntityDialog, {
    props: { projectId: "p1", agentId },
    global: { stubs: { Teleport: true } },
  });
}
function button(wrapper: ReturnType<typeof render>, text: string) {
  return wrapper.findAll("button").find((b) => b.text() === text)!;
}
beforeEach(() => {
  vi.resetAllMocks();
  setActivePinia(createPinia());
  useConnectionStore().setConnected();
  useProjectsStore().setProjectsFromList([
    {
      project_id: "p1",
      name: "Project",
      status: "active",
      cluster_ids: ["c1"],
      agents: [],
    } as never,
  ]);
  api.snapshot.mockResolvedValue(snapshot());
  api.list.mockResolvedValue({
    success: true,
    data: { items: [], available_ability_types: ["send_message", "conversation"] },
  });
  api.listSessions.mockResolvedValue({
    active_session_id: "s1",
    sessions: [
      { session_id: "s1", name: "Saved session", active_branch_id: "b1", lifecycle: "active" },
    ],
    branches: [{ branch_id: "b1", session_id: "s1", name: "main", lifecycle: "active" }],
  });
  api.getHistory.mockResolvedValue({
    nodes: [
      { id: "n1", iteration: 1, messages_delta: [{ content: "saved work" }], action_results: [] },
    ],
  });
  api.listInbox.mockResolvedValue({ items: [], next_before_seq: null });
});
it("reads saved sessions and history while offline without starting the actor", async () => {
  const w = render();
  await flushPromises();
  expect(w.text()).toContain("Offline");
  await button(w, "Sessions & history").trigger("click");
  await flushPromises();
  expect(w.text()).toContain("Saved session");
  await button(w, "Load history").trigger("click");
  await flushPromises();
  expect(w.text()).toContain("saved work");
  expect(api.start).not.toHaveBeenCalled();
  expect(button(w, "Create session").attributes("disabled")).toBeDefined();
  w.unmount();
});
it("saves persistent selected types with the observed revision", async () => {
  const w = render();
  await flushPromises();
  await button(w, "Abilities").trigger("click");
  await w.find('input[value="conversation"]').setValue(true);
  await button(w, "Save ability configuration").trigger("click");
  await flushPromises();
  expect(api.configure).toHaveBeenCalledWith(["send_message", "conversation"], 5);
  expect(w.emitted("changed")).toBeTruthy();
  w.unmount();
});
it("requires confirmation before stopping and uses the runtime incarnation", async () => {
  api.snapshot.mockResolvedValue(snapshot(true));
  const w = render();
  await flushPromises();
  await button(w, "Stop agent").trigger("click");
  expect(api.stop).not.toHaveBeenCalled();
  await button(w, "Confirm operation").trigger("click");
  await flushPromises();
  expect(api.stop).toHaveBeenCalledWith(5, "instance-1");
  w.unmount();
});
it("unlocks waiting but ignores a late mutation response until a state refresh", async () => {
  let resolve!: (value: unknown) => void;
  api.start.mockReturnValue(
    new Promise((r) => {
      resolve = r;
    }),
  );
  const w = render();
  await flushPromises();
  await button(w, "Start agent").trigger("click");
  await button(w, "Stop waiting").trigger("click");
  expect(w.text()).toContain("Receipt is uncertain");
  expect(button(w, "Start agent").attributes("disabled")).toBeDefined();
  resolve({});
  await flushPromises();
  expect(w.emitted("changed")).toBeUndefined();
  await button(w, "Refresh state").trigger("click");
  await flushPromises();
  expect(button(w, "Start agent").attributes("disabled")).toBeUndefined();
  w.unmount();
});
it("shows errors, requires refreshing a failed mutation, and preserves the form", async () => {
  api.start.mockRejectedValue(new Error("agent_start_failed: missing model"));
  const w = render();
  await flushPromises();
  await button(w, "Start agent").trigger("click");
  await flushPromises();
  expect(w.find('[role="alert"]').text()).toContain("missing model");
  expect(button(w, "Refresh state").attributes("disabled")).toBeUndefined();
  w.unmount();
});
it("creates a persistent entity with explicit start and selected cluster", async () => {
  const w = mount(AgentEntityDialog, {
    props: { projectId: "p1" },
    global: { stubs: { Teleport: true } },
  });
  await flushPromises();
  await w.find("input[data-autofocus]").setValue("new-agent");
  await w.find("select").setValue("c1");
  await w.find('input[value="send_message"]').setValue(true);
  api.create.mockResolvedValue({ success: false, error: "name already exists" });
  await w.find("form").trigger("submit");
  await flushPromises();
  expect(api.create).toHaveBeenCalledWith(
    expect.objectContaining({
      project_id: "p1",
      cluster_id: "c1",
      name: "new-agent",
      start: false,
      ability_types: ["send_message"],
    }),
  );
  expect(w.text()).toContain("name already exists");
  w.unmount();
});
it("paginates messages and explicitly confirms retry for this recipient", async () => {
  api.snapshot.mockResolvedValue(snapshot(true));
  const item = {
    message: { message_id: "m1", sender_id: "other", content: "hello" },
    deliveries: [
      {
        delivery_id: "d1",
        recipient_id: "a1",
        state: "failed",
        error: "solver failed",
        blocked_reason: null,
        consumed_node_id: null,
      },
    ],
  };
  api.listInbox
    .mockResolvedValueOnce({ items: [item], next_before_seq: 4 })
    .mockResolvedValue({ items: [], next_before_seq: null });
  const w = render();
  await flushPromises();
  await button(w, "Messages").trigger("click");
  await flushPromises();
  expect(w.text()).toContain("solver failed");
  await button(w, "Load older messages").trigger("click");
  await flushPromises();
  expect(api.listInbox).toHaveBeenLastCalledWith(20, 4);
  await button(w, "Retry delivery").trigger("click");
  expect(api.retry).not.toHaveBeenCalled();
  await button(w, "Confirm operation").trigger("click");
  await flushPromises();
  expect(api.retry).toHaveBeenCalledWith("d1");
  w.unmount();
});

it("disables mutations when disconnected and ignores a disposed request", async () => {
  let resolve!: (value: unknown) => void;
  api.snapshot.mockReturnValue(
    new Promise((r) => {
      resolve = r;
    }),
  );
  const w = render();
  await flushPromises();
  useConnectionStore().setDisconnected();
  await flushPromises();
  expect(w.text()).toContain("Stopped waiting locally");
  w.unmount();
  resolve(snapshot());
  await flushPromises();
  expect(api.start).not.toHaveBeenCalled();
});

it("opens the created identity after a successful creation receipt", async () => {
  const w = mount(AgentEntityDialog, {
    props: { projectId: "p1" },
    global: { stubs: { Teleport: true } },
  });
  await flushPromises();
  await w.find("input[data-autofocus]").setValue("new-agent");
  await w.find("select").setValue("c1");
  await w.find('input[value="send_message"]').setValue(true);
  api.create.mockResolvedValue({ success: true, data: snapshot() });
  await w.find("form").trigger("submit");
  await flushPromises();
  expect(w.emitted("created")).toEqual([["a1"]]);
  w.unmount();
});
