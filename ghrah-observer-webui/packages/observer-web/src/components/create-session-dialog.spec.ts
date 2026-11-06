// @vitest-environment happy-dom
import { createPinia, setActivePinia } from "pinia";
import { flushPromises, mount, type VueWrapper } from "@vue/test-utils";
import { afterEach, beforeEach, expect, it, vi } from "vitest";
import { useObserver } from "@/composables/useObserver";
import CreateSessionDialog from "./create-session-dialog.vue";
vi.mock("@/composables/usePlugins", () => ({ usePlugins: vi.fn() }));
const agent = { projectId: "p1", agentId: "a1", agentName: "Helper" };
let wrapper: VueWrapper | undefined;
function setup(route: "entity" | "chain" = "chain") {
  const observer = useObserver();
  let saved = "Agent default prompt";
  const createSession = vi.fn().mockImplementation(async (_target, options) => {
    saved = options.systemPrompt ?? "Agent default prompt";
    return { success: true, data: { session_id: "s2", system_prompt: saved } };
  });
  const entityCreate = vi.fn().mockImplementation(async (_name, prompt) => {
    saved = prompt ?? "Agent default prompt";
    return { session: { session_id: "s2", system_prompt: saved } };
  });
  const listSessions = vi.fn().mockImplementation(async () => ({
    success: true,
    data: { sessions: [{ session_id: "s2", system_prompt: saved }] },
  }));
  const client = {
    entityCreate,
    createSession,
    listSessions,
    agent: vi.fn().mockReturnValue({ context: { createSession: entityCreate } }),
  };
  observer.client.value = client as unknown as NonNullable<typeof observer.client.value>;
  observer.connection.setConnected();
  wrapper = mount(CreateSessionDialog, { props: { agent, route }, attachTo: document.body });
  return { client, observer };
}
function button(text: string) {
  return wrapper!.findAll("button").find((x) => x.text() === text)!;
}
beforeEach(() => {
  setActivePinia(createPinia());
  useObserver().error.value = null;
});
afterEach(() => {
  wrapper?.unmount();
  wrapper = undefined;
  useObserver().client.value = null;
});
it.each(["chain", "entity"] as const)(
  "uses the Agent default when the option is off (%s)",
  async (route) => {
    const { client } = setup(route);
    await button("Create session").trigger("click");
    await flushPromises();
    if (route === "chain")
      expect(client.createSession).toHaveBeenCalledWith(
        agent,
        expect.objectContaining({ systemPrompt: undefined }),
      );
    else expect(client.entityCreate).toHaveBeenCalledWith(undefined, undefined);
    expect(client.listSessions).toHaveBeenCalledWith(agent);
    expect(wrapper!.text()).toContain("Created and verified");
    expect(wrapper!.get("textarea").element).toHaveProperty("value", "Agent default prompt");
    expect(wrapper!.emitted("created")).toEqual([["s2"]]);
  },
);
it.each(["chain", "entity"] as const)(
  "preserves explicit empty and multiline prompts (%s)",
  async (route) => {
    for (const prompt of ["", "  Exact prompt\nnext line\n"]) {
      const { client } = setup(route);
      await wrapper!.get('input[type="checkbox"]').setValue(true);
      await wrapper!.get("textarea").setValue(prompt);
      await button("Create session").trigger("click");
      await flushPromises();
      if (route === "chain")
        expect(client.createSession).toHaveBeenCalledWith(
          agent,
          expect.objectContaining({ systemPrompt: prompt }),
        );
      else expect(client.entityCreate).toHaveBeenCalledWith(undefined, prompt);
      expect(wrapper!.get("textarea").element).toHaveProperty("value", prompt);
      expect(wrapper!.get("textarea").element).toHaveProperty("readOnly", true);
      wrapper!.unmount();
      wrapper = undefined;
    }
  },
);
it("retries only read-back when creation succeeded but reading failed", async () => {
  const { client } = setup();
  client.listSessions.mockRejectedValueOnce(new Error("Read failed"));
  await button("Create session").trigger("click");
  await flushPromises();
  expect(wrapper!.text()).toContain("Unable to read back the created session");
  expect(button("Create session")).toBeUndefined();
  await button("Check created session").trigger("click");
  await flushPromises();
  expect(client.createSession).toHaveBeenCalledTimes(1);
  expect(wrapper!.text()).toContain("Created and verified");
});
it("retains input on rejection and disables submission after disconnect", async () => {
  const { client, observer } = setup();
  client.createSession.mockResolvedValue({ success: false, error: "agent_busy" });
  await wrapper!.get('input[type="checkbox"]').setValue(true);
  await wrapper!.get("textarea").setValue("Keep me");
  await button("Create session").trigger("click");
  await flushPromises();
  expect(wrapper!.text()).toContain("agent_busy");
  expect(wrapper!.get("textarea").element).toHaveProperty("value", "Keep me");
  observer.connection.setDisconnected();
  await flushPromises();
  expect(button("Create session").attributes("disabled")).toBeDefined();
});
it("blocks duplicate creation after an uncertain transport result", async () => {
  const { client } = setup();
  client.createSession.mockRejectedValue(new Error("Transport lost"));
  await button("Create session").trigger("click");
  await flushPromises();
  expect(wrapper!.text()).toContain("Creation could not be confirmed");
  expect(button("Create session")).toBeUndefined();
  expect(client.createSession).toHaveBeenCalledTimes(1);
});
it("rejects a different saved prompt and keeps read-only verification available", async () => {
  const { client } = setup();
  client.listSessions.mockResolvedValue({
    success: true,
    data: { sessions: [{ session_id: "s2", system_prompt: "different" }] },
  });
  await wrapper!.get('input[type="checkbox"]').setValue(true);
  await wrapper!.get("textarea").setValue("Expected");
  await button("Create session").trigger("click");
  await flushPromises();
  expect(wrapper!.text()).toContain("differs from the submitted prompt");
  expect(wrapper!.emitted("created")).toBeUndefined();
  expect(button("Check created session")).toBeDefined();
});

it("requires confirmation to discard a draft and ignores a late response after closing", async () => {
  const { client } = setup();
  await wrapper!.get('input[type="checkbox"]').setValue(true);
  await wrapper!.get("textarea").setValue("Unsaved prompt");
  await button("Close").trigger("click");
  expect(wrapper!.findAll('[role="dialog"]')).toHaveLength(2);
  await button("Cancel").trigger("click");
  expect(wrapper!.get("textarea").element).toHaveProperty("value", "Unsaved prompt");
  let finish!: (value: unknown) => void;
  client.createSession.mockImplementationOnce(
    () =>
      new Promise((resolve) => {
        finish = resolve;
      }),
  );
  await button("Create session").trigger("click");
  const old = wrapper!;
  old.unmount();
  wrapper = undefined;
  finish({ success: true, data: { session_id: "late" } });
  await flushPromises();
  expect(old.emitted("created")).toBeUndefined();
  expect(client.listSessions).not.toHaveBeenCalled();
});
