// @vitest-environment happy-dom
import { useConnectionStore, useProjectsStore } from "@ghrah/observer-core";
import { flushPromises, mount } from "@vue/test-utils";
import { createPinia, setActivePinia } from "pinia";
import { beforeEach, describe, expect, it, vi } from "vitest";
import { ref } from "vue";
import AgentCommunicationPanel from "./agent-communication-panel.vue";

const send = vi.hoisted(() => vi.fn());
vi.mock("@/composables/useObserver", () => ({
  useObserver: () => ({ sendAgentMessage: send, error: ref(null) }),
}));

beforeEach(() => {
  setActivePinia(createPinia());
  send.mockReset();
  useConnectionStore().setConnected();
  useProjectsStore().setProjectsFromList([
    {
      project_id: "p",
      name: "demo",
      status: "active",
      agents: [
        { name: "A", agent_id: "a", cluster_id: "c1" },
        { name: "B", agent_id: "b", cluster_id: "c2" },
        { name: "C", agent_id: "c", cluster_id: "c2" },
      ],
    } as never,
  ]);
});

async function configure(schedule = "parallel") {
  const wrapper = mount(AgentCommunicationPanel, { props: { projectId: "p" } });
  await wrapper.find("textarea").setValue("verify");
  await wrapper.findAll("select")[0].setValue("a");
  await wrapper.findAll("select")[1].setValue("b");
  await wrapper.findAll("select")[3].setValue(schedule);
  await wrapper.findAll("button")[1].trigger("click");
  await wrapper.findAll("select")[2].setValue(schedule === "sequence" ? "b" : "a");
  await wrapper.findAll("select")[3].setValue("c");
  return wrapper;
}

describe("AgentCommunicationPanel", () => {
  it("runs a cross-cluster fan-out using stable IDs", async () => {
    send.mockResolvedValue({ success: true, data: { message_id: "m1" } });
    const wrapper = await configure();
    await wrapper.find(".btn-primary").trigger("click");
    await flushPromises();
    expect(send).toHaveBeenCalledTimes(2);
    expect(send.mock.calls[0]).toEqual([
      { projectId: "p", agentId: "a", agentName: "A" },
      { projectId: "p", agentId: "b", agentName: "B" },
      "verify",
      false,
    ]);
    expect(send.mock.calls[1][1].agentId).toBe("c");
  });

  it("waits for A to B before B to C and stops after a failure", async () => {
    let finish!: (value: unknown) => void;
    send.mockImplementationOnce(
      () =>
        new Promise((resolve) => {
          finish = resolve;
        }),
    );
    const wrapper = await configure("sequence");
    await wrapper.find(".btn-primary").trigger("click");
    expect(send).toHaveBeenCalledTimes(1);
    finish({ success: false, error: "failed" });
    await flushPromises();
    expect(send).toHaveBeenCalledTimes(1);
    expect(wrapper.text()).toContain("Skipped");
  });

  it("marks async receipt as committed, not a completed recipient", async () => {
    send.mockResolvedValue({ success: true, data: { status: "prepared" } });
    const wrapper = await configure();
    await wrapper.findAll("select")[4].setValue("async");
    await wrapper.find(".btn-primary").trigger("click");
    await flushPromises();
    expect(send.mock.calls[0][3]).toBe(true);
    expect(wrapper.text()).toContain("Committed for delivery");
    expect(wrapper.text()).not.toContain("Reply received");
  });

  it("does not continue an ordered run when the receipt is unknown", async () => {
    send.mockResolvedValue(null);
    const wrapper = await configure("sequence");
    await wrapper.find(".btn-primary").trigger("click");
    await flushPromises();
    expect(send).toHaveBeenCalledTimes(1);
    expect(wrapper.text()).toContain("Receipt unknown");
  });
});

it("explains a missing ability and immediately unlocks the form", async () => {
  send.mockResolvedValue({ success: false, error: "source_ability_missing" });
  const wrapper = await configure();
  await wrapper.find(".btn-primary").trigger("click");
  await flushPromises();
  expect(wrapper.text()).toContain("Source agent A has no send_message ability");
  expect(wrapper.find("fieldset").attributes("disabled")).toBeUndefined();
  expect(wrapper.find(".btn-primary").attributes("disabled")).toBeUndefined();
});

it("stops waiting, skips unsent links, and ignores late results from an older run", async () => {
  let finish!: (value: unknown) => void;
  send.mockImplementationOnce(
    () =>
      new Promise((resolve) => {
        finish = resolve;
      }),
  );
  const wrapper = await configure("sequence");
  await wrapper.find(".btn-primary").trigger("click");
  expect(wrapper.find("fieldset").attributes("disabled")).toBeDefined();
  const stop = wrapper.findAll("button").find((button) => button.text() === "Stop waiting")!;
  await stop.trigger("click");
  expect(wrapper.find("fieldset").attributes("disabled")).toBeUndefined();
  expect(wrapper.text()).toContain("Stopped waiting locally");
  expect(wrapper.text()).toContain("Skipped");
  send.mockResolvedValue({ success: false, error: "new failure" });
  await wrapper.find(".btn-primary").trigger("click");
  await flushPromises();
  finish({ success: true, data: { response: "late reply" } });
  await flushPromises();
  expect(wrapper.text()).toContain("new failure");
  expect(wrapper.text()).not.toContain("late reply");
  expect(send).toHaveBeenCalledTimes(2);
});

it("shows why verification is unavailable instead of a silent disabled button", async () => {
  const wrapper = mount(AgentCommunicationPanel, { props: { projectId: "p" } });
  expect(wrapper.text()).toContain("Enter a message");
  useConnectionStore().setDisconnected();
  await flushPromises();
  expect(wrapper.text()).toContain("Connect to the server");
});

it("shows an actionable queue blocker instead of only the raw failure", async () => {
  send.mockResolvedValue({
    success: false,
    error: "message_queue_blocked: blocking_delivery=d1, reason=missing ability",
  });
  const wrapper = await configure();
  await wrapper.find(".btn-primary").trigger("click");
  await flushPromises();
  expect(wrapper.text()).toContain("session queue is blocked");
  expect(wrapper.text()).toContain("blocking_delivery=d1");
  expect(wrapper.text()).toContain("create and activate a new session");
});
