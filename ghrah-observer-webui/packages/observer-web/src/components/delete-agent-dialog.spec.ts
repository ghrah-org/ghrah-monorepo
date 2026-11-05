// @vitest-environment happy-dom
import { createPinia, setActivePinia } from "pinia";
import { flushPromises, mount, type VueWrapper } from "@vue/test-utils";
import { afterEach, beforeEach, expect, it, vi } from "vitest";
import { useObserver } from "@/composables/useObserver";
import DeleteAgentDialog from "./delete-agent-dialog.vue";
vi.mock("@/composables/usePlugins", () => ({ usePlugins: vi.fn() }));
const agent = { projectId: "p1", agentId: "a1", agentName: "Agent" };
let w: VueWrapper | undefined;
function setup() {
  const observer = useObserver();
  const client = {
    removeProjectAgent: vi.fn().mockResolvedValue({ success: true }),
    getProject: vi.fn().mockResolvedValue({
      success: true,
      data: { project: { project_id: "p1", name: "Project", agents: [] } },
    }),
  };
  observer.client.value = client as unknown as NonNullable<typeof observer.client.value>;
  observer.connection.setConnected();
  w = mount(DeleteAgentDialog, { props: { agent, expectedVersion: 7 }, attachTo: document.body });
  return { client, observer };
}
function button(text: string) {
  return w!.findAll("button").find((x) => x.text() === text)!;
}
async function confirm() {
  await w!.get("input").setValue("Agent");
  await button("Delete this entity").trigger("click");
  await flushPromises();
}
beforeEach(() => {
  setActivePinia(createPinia());
  useObserver().error.value = null;
});
afterEach(() => {
  w?.unmount();
  w = undefined;
  useObserver().client.value = null;
});
it("requires the exact name and sends stable identity with the observed version", async () => {
  const { client } = setup();
  expect(button("Delete this entity").attributes("disabled")).toBeDefined();
  await w!.get("input").setValue("Other");
  expect(button("Delete this entity").attributes("disabled")).toBeDefined();
  await confirm();
  expect(client.removeProjectAgent).toHaveBeenCalledWith(agent, 7);
  expect(client.getProject).toHaveBeenCalledWith("p1");
  expect(w!.emitted("deleted")).toEqual([[]]);
  expect(w!.text()).toContain("does not automatically delete session history");
});
it("cancels without sending a delete request", async () => {
  const { client } = setup();
  await button("Cancel").trigger("click");
  expect(client.removeProjectAgent).not.toHaveBeenCalled();
  expect(w!.emitted("close")).toHaveLength(1);
});
it.each(["agent_terminate_failed", "Project p1 modified: expected version 7, got 8"])(
  "preserves the entity on rejection: %s",
  async (error) => {
    const { client } = setup();
    client.removeProjectAgent.mockResolvedValue({ success: false, error });
    await confirm();
    expect(w!.text()).toContain(error);
    expect(w!.emitted("deleted")).toBeUndefined();
    expect(client.getProject).not.toHaveBeenCalled();
    expect(button("Delete this entity")).toBeUndefined();
    expect(w!.text()).toContain("refresh state");
  },
);
it("retries read-back after accepted deletion without deleting twice", async () => {
  const { client } = setup();
  client.getProject.mockRejectedValueOnce(new Error("Read failed"));
  await confirm();
  expect(w!.text()).toContain("Read failed");
  expect(w!.emitted("deleted")).toBeUndefined();
  await button("Check deletion result").trigger("click");
  await flushPromises();
  expect(client.removeProjectAgent).toHaveBeenCalledTimes(1);
  expect(w!.emitted("deleted")).toHaveLength(1);
});
it("checks an uncertain request without resubmitting deletion", async () => {
  const { client } = setup();
  client.removeProjectAgent.mockRejectedValueOnce(new Error("Transport lost"));
  await confirm();
  expect(w!.text()).toContain("could not be confirmed");
  await button("Check deletion result").trigger("click");
  await flushPromises();
  expect(client.removeProjectAgent).toHaveBeenCalledTimes(1);
  expect(w!.emitted("deleted")).toHaveLength(1);
});
it("does not claim success when authority still contains the stable Agent ID", async () => {
  const { client } = setup();
  client.getProject.mockResolvedValue({
    success: true,
    data: {
      project: {
        project_id: "p1",
        name: "Project",
        agents: [{ agent_id: "a1", name: "Agent", cluster_id: "c1" }],
      },
    },
  });
  await confirm();
  expect(w!.text()).toContain("entity still exists");
  expect(w!.emitted("deleted")).toBeUndefined();
  expect(button("Delete this entity")).toBeUndefined();
});
it("blocks disconnected requests and ignores a late deletion receipt after unmount", async () => {
  const { client, observer } = setup();
  await w!.get("input").setValue("Agent");
  observer.connection.setDisconnected();
  await flushPromises();
  expect(button("Delete this entity").attributes("disabled")).toBeDefined();
  observer.connection.setConnected();
  await flushPromises();
  let finish!: (value: unknown) => void;
  client.removeProjectAgent.mockImplementationOnce(
    () =>
      new Promise((resolve) => {
        finish = resolve;
      }),
  );
  await button("Delete this entity").trigger("click");
  const old = w!;
  old.unmount();
  w = undefined;
  finish({ success: true });
  await flushPromises();
  expect(old.emitted("deleted")).toBeUndefined();
  expect(client.getProject).not.toHaveBeenCalled();
});
