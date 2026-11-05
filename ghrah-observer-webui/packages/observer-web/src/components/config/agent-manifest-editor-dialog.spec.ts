// @vitest-environment happy-dom

import { createPinia, setActivePinia } from "pinia";
import { flushPromises, mount, type VueWrapper } from "@vue/test-utils";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import { useObserver } from "@/composables/useObserver";
import fixture from "../../../../protocol/src/__fixtures__/manifest-responses.json";
import AgentConfigPage from "./agent-config-page.vue";
import AgentManifestEditorDialog from "./agent-manifest-editor-dialog.vue";
import NewAgentManifestDialog from "./new-agent-manifest-dialog.vue";

vi.mock("@/composables/usePlugins", () => ({ usePlugins: vi.fn() }));

const fullName = "audit.helper";
let wrapper: VueWrapper | undefined;
let serverSource: string;
let serverSourceHash: string;
let serverManifest: typeof fixture.agent.manifest;

function setupClient() {
  const observer = useObserver();
  const client = {
    getAgent: vi.fn().mockImplementation(async () => ({
      success: true,
      data: { manifest: serverManifest, source: serverSource, source_hash: serverSourceHash },
    })),
    putAgent: vi.fn().mockImplementation(async (_name: string, source: string) => {
      serverSource = source;
      serverSourceHash = `sha256:${source}`;
      return {
        success: true,
        data: { manifest: serverManifest, source, source_hash: serverSourceHash },
      };
    }),
    validateManifest: vi
      .fn()
      .mockResolvedValue({ success: true, data: { valid: true, errors: [] } }),
    resolveAgent: vi.fn().mockResolvedValue({ success: true, data: fixture.resolved_agent }),
    listManifestAgents: vi.fn().mockResolvedValue({ success: true, data: { agents: [] } }),
  };
  observer.client.value = client as unknown as NonNullable<typeof observer.client.value>;
  observer.connection.setConnected();
  return { observer, client };
}

async function open(mode: "edit" | "view" = "edit") {
  wrapper = mount(AgentManifestEditorDialog, {
    props: { fullName, initialMode: mode },
    attachTo: document.body,
  });
  await flushPromises();
  return wrapper;
}

function button(text: string) {
  const found = wrapper!.findAll("button").find((item) => item.text() === text);
  if (!found) throw new Error(`Missing button: ${text}`);
  return found;
}

beforeEach(() => {
  setActivePinia(createPinia());
  serverSource = fixture.agent.source;
  serverSourceHash = fixture.agent.source_hash;
  serverManifest = structuredClone(fixture.agent.manifest);
  const observer = useObserver();
  observer.client.value = null;
  observer.error.value = null;
});
afterEach(() => {
  wrapper?.unmount();
  wrapper = undefined;
  useObserver().client.value = null;
});

describe("Agent Manifest editor", () => {
  it("reads the complete original source, including comments, in view mode", async () => {
    const { client } = setupClient();
    serverSource += "# " + "long prompt content ".repeat(80);
    await open("view");
    const field = wrapper!.get("textarea").element as HTMLTextAreaElement;
    expect(field.value).toBe(serverSource);
    expect(field.readOnly).toBe(true);
    expect(client.getAgent).toHaveBeenCalledWith(fullName);
    await button("Edit YAML").trigger("click");
    expect(field.readOnly).toBe(false);
    expect(client.putAgent).not.toHaveBeenCalled();
  });

  it("validates the exact draft, saves it and reads the authority back", async () => {
    const { client, observer } = setupClient();
    await open();
    const draft = serverSource + "\n# Keep comments and untouched settings.\n";
    await wrapper!.get("textarea").setValue(draft);
    await button("Validate and save").trigger("click");
    await flushPromises();
    expect(client.validateManifest).toHaveBeenCalledWith(draft, "agent");
    expect(client.putAgent).toHaveBeenCalledWith(fullName, draft, true, fixture.agent.source_hash);
    expect(client.getAgent).toHaveBeenCalledTimes(2);
    expect(client.validateManifest.mock.invocationCallOrder[0]).toBeLessThan(
      client.putAgent.mock.invocationCallOrder[0],
    );
    expect(wrapper!.get("textarea").element).toHaveProperty("value", draft);
    expect(wrapper!.text()).toContain("Saved and verified from the server.");
    expect(observer.manifests.agentDetails.get(fullName)?.source).toBe(draft);
    expect(wrapper!.emitted("dirtyChange")?.at(-1)).toEqual([false]);
  });

  it("reports invalid structure and never writes it", async () => {
    const { client } = setupClient();
    client.validateManifest.mockResolvedValue({
      success: true,
      data: { valid: false, errors: ["model.endpoint_name: Field required"] },
    });
    await open();
    await wrapper!.get("textarea").setValue("manifest: agent\n");
    await button("Validate and save").trigger("click");
    await flushPromises();
    expect(wrapper!.get('[role="alert"]').text()).toContain("model.endpoint_name");
    expect(client.putAgent).not.toHaveBeenCalled();
    expect(wrapper!.get("textarea").element).toHaveProperty("value", "manifest: agent\n");
  });

  it("supports validation without saving and clears stale validation after edits", async () => {
    const { client } = setupClient();
    await open();
    await button("Validate").trigger("click");
    await flushPromises();
    expect(wrapper!.text()).toContain("Manifest structure is valid.");
    await wrapper!.get("textarea").setValue(serverSource + "# Changed\n");
    expect(wrapper!.text()).not.toContain("Manifest structure is valid.");
    expect(client.putAgent).not.toHaveBeenCalled();
  });

  it("preserves the draft on an explicit save rejection", async () => {
    const { client } = setupClient();
    client.putAgent.mockResolvedValue({
      success: false,
      error: "metadata identity must match target",
    });
    await open();
    const draft = serverSource + "# Draft\n";
    await wrapper!.get("textarea").setValue(draft);
    await button("Validate and save").trigger("click");
    await flushPromises();
    expect(wrapper!.get('[role="alert"]').text()).toContain("identity must match");
    expect(wrapper!.get("textarea").element).toHaveProperty("value", draft);
    expect(wrapper!.text()).not.toContain("Saved and verified");
  });

  it("keeps the draft when the server source changed before saving", async () => {
    const { client } = setupClient();
    client.putAgent.mockResolvedValue({
      success: false,
      error: "Manifest changed on the server; reload the current source before saving",
      error_detail: `code=manifest_conflict; expected_source_hash=${fixture.agent.source_hash}; current_source_hash=sha256:different`,
    });
    await open();
    const draft = serverSource + "# Concurrent edit\n";
    await wrapper!.get("textarea").setValue(draft);
    await button("Validate and save").trigger("click");
    await flushPromises();
    expect(client.putAgent).toHaveBeenCalledWith(fullName, draft, true, fixture.agent.source_hash);
    expect(wrapper!.get('[role="alert"]').text()).toContain("changed on the server");
    expect(wrapper!.get("textarea").element).toHaveProperty("value", draft);
    expect(client.getAgent).toHaveBeenCalledTimes(1);
  });

  it("retries read-back without writing twice when the save was accepted", async () => {
    const { client } = setupClient();
    await open();
    client.getAgent.mockRejectedValueOnce(new Error("Connection lost"));
    await wrapper!.get("textarea").setValue(serverSource + "# Draft\n");
    await button("Validate and save").trigger("click");
    await flushPromises();
    expect(wrapper!.text()).toContain("reading it back failed");
    await button("Check saved file").trigger("click");
    await flushPromises();
    expect(client.putAgent).toHaveBeenCalledTimes(1);
    expect(wrapper!.text()).toContain("Saved and verified from the server.");
  });

  it("keeps the draft if the server differs after an uncertain save", async () => {
    const { client } = setupClient();
    client.putAgent.mockRejectedValueOnce(new Error("Connection lost"));
    await open();
    const draft = serverSource + "# Draft\n";
    await wrapper!.get("textarea").setValue(draft);
    await button("Validate and save").trigger("click");
    await flushPromises();
    expect(wrapper!.text()).toContain("could not be confirmed");
    await button("Check saved file").trigger("click");
    await flushPromises();
    expect(wrapper!.get("textarea").element).toHaveProperty("value", draft);
    expect(wrapper!.text()).toContain("differs from the submitted draft");
    expect(wrapper!.text()).not.toContain("Saved and verified");
    expect(client.putAgent).toHaveBeenCalledTimes(1);
  });

  it("keeps unsaved input after disconnect and disables network actions", async () => {
    const { observer } = setupClient();
    await open();
    const draft = serverSource + "# Draft\n";
    await wrapper!.get("textarea").setValue(draft);
    observer.connection.setDisconnected();
    await flushPromises();
    expect(button("Validate and save").attributes("disabled")).toBeDefined();
    expect(button("Validate").attributes("disabled")).toBeDefined();
    expect(button("Resolve preview").attributes("disabled")).toBeDefined();
    expect(wrapper!.get("textarea").element).toHaveProperty("value", draft);
  });

  it("guards Escape, browser unload and discarding unsaved changes", async () => {
    setupClient();
    await open();
    await wrapper!.get("textarea").setValue(serverSource + "# Draft\n");
    const unload = new Event("beforeunload", { cancelable: true });
    window.dispatchEvent(unload);
    expect(unload.defaultPrevented).toBe(true);
    await wrapper!.get("textarea").trigger("keydown", { key: "Escape" });
    expect(wrapper!.findAll('[role="dialog"]')).toHaveLength(2);
    await button("Cancel").trigger("click");
    expect(wrapper!.emitted("close")).toBeUndefined();
    await button("Close").trigger("click");
    await button("Discard changes").trigger("click");
    expect(wrapper!.emitted("close")).toHaveLength(1);
  });

  it("reports read failure and can retry without using stale cached source", async () => {
    const { client } = setupClient();
    client.getAgent.mockRejectedValueOnce(new Error("Read failed"));
    await open();
    expect(wrapper!.text()).toContain("Read failed");
    expect(wrapper!.find("textarea").exists()).toBe(false);
    await button("Retry loading").trigger("click");
    await flushPromises();
    expect(wrapper!.get("textarea").element).toHaveProperty("value", serverSource);
  });

  it("ignores a late read after the dialog is unmounted", async () => {
    const { client } = setupClient();
    let resolve!: (value: unknown) => void;
    client.getAgent.mockReturnValueOnce(
      new Promise((done) => {
        resolve = done;
      }),
    );
    await open();
    const instance = wrapper!;
    instance.unmount();
    wrapper = undefined;
    resolve({ success: true, data: fixture.agent });
    await flushPromises();
    expect(instance.emitted("dirtyChange")).toBeUndefined();
    expect(instance.emitted("close")).toBeUndefined();
  });
});

describe("Agent Manifest resolve preview", () => {
  it.each(["legacy", "builtin"])(
    "shows the server-selected %s mode and declaration permissions",
    async (mode) => {
      const { client } = setupClient();
      client.resolveAgent.mockResolvedValue({
        success: true,
        data: { ...fixture.resolved_agent, ability_source: mode },
      });
      await open("view");
      await button("Resolve preview").trigger("click");
      await flushPromises();
      const region = wrapper!.get('[role="region"]');
      expect(region.text()).toContain(mode);
      expect(region.text()).toContain("audit.fs.read_file");
      expect(region.text()).toContain("test-model");
      expect(region.text()).toContain("Merged declaration permissions");
      expect(region.text()).toContain('"require_hitl": true');
      expect(client.putAgent).not.toHaveBeenCalled();
      expect(client.getAgent).toHaveBeenCalledTimes(1);
    },
  );

  it("previews the exact unsaved draft and clears the result after another edit", async () => {
    const { client } = setupClient();
    await open();
    const original = serverSource;
    const draft = original.replace("Keep the entire prompt.", "Unsaved preview prompt.");
    await wrapper!.get("textarea").setValue(draft);
    await button("Resolve preview").trigger("click");
    await flushPromises();
    expect(client.resolveAgent).toHaveBeenCalledWith(fullName, null, draft);
    expect(wrapper!.find('[role="region"]').exists()).toBe(true);
    expect(serverSource).toBe(original);
    expect(client.putAgent).not.toHaveBeenCalled();
    await wrapper!.get("textarea").setValue(draft + "# Changed after preview\n");
    expect(wrapper!.find('[role="region"]').exists()).toBe(false);
  });

  it("keeps input and reports an unresolved ability without saving", async () => {
    const { client } = setupClient();
    client.resolveAgent.mockResolvedValue({
      success: false,
      error: "Unknown builtin ability type: missing_type",
    });
    await open();
    await button("Resolve preview").trigger("click");
    await flushPromises();
    expect(wrapper!.get('[role="alert"]').text()).toContain("missing_type");
    expect(wrapper!.get("textarea").element).toHaveProperty("value", serverSource);
    expect(wrapper!.find('[role="region"]').exists()).toBe(false);
    expect(client.putAgent).not.toHaveBeenCalled();
  });

  it.each([
    { ...fixture.resolved_agent, agent_full_name: "other.agent" },
    { ...fixture.resolved_agent, input_source: "stored" },
    { config: fixture.resolved_agent.config, abilities: fixture.resolved_agent.abilities },
  ])("rejects a mismatched or old server response", async (data) => {
    const { client } = setupClient();
    client.resolveAgent.mockResolvedValue({ success: true, data });
    await open();
    await button("Resolve preview").trigger("click");
    await flushPromises();
    expect(wrapper!.get('[role="alert"]').text()).toContain("invalid response");
    expect(wrapper!.find('[role="region"]').exists()).toBe(false);
    expect(client.putAgent).not.toHaveBeenCalled();
  });

  it("locks the input while waiting and ignores results after unmount", async () => {
    const { client } = setupClient();
    let complete!: (value: unknown) => void;
    client.resolveAgent.mockReturnValueOnce(
      new Promise((resolve) => {
        complete = resolve;
      }),
    );
    await open();
    await button("Resolve preview").trigger("click");
    expect(wrapper!.get("textarea").attributes("disabled")).toBeDefined();
    const instance = wrapper!;
    instance.unmount();
    wrapper = undefined;
    complete({ success: true, data: fixture.resolved_agent });
    await flushPromises();
    expect(instance.emitted("close")).toBeUndefined();
    expect(instance.emitted("dirtyChange")).toBeUndefined();
    expect(client.putAgent).not.toHaveBeenCalled();
  });
});

describe("Agent configuration page wiring", () => {
  it("opens the in-page editor and refreshes the selected summary after read-back", async () => {
    const { observer } = setupClient();
    await observer.getAgent(fullName);
    wrapper = mount(AgentConfigPage, { attachTo: document.body });
    await flushPromises();
    await button("Edit YAML").trigger("click");
    await flushPromises();
    expect(wrapper.findComponent(AgentManifestEditorDialog).exists()).toBe(true);
    serverManifest = { ...serverManifest, system_prompt: "Updated prompt ".repeat(100) };
    await wrapper.get("textarea").setValue(serverSource + "# Saved\n");
    await button("Validate and save").trigger("click");
    await flushPromises();
    await button("Close").trigger("click");
    expect(wrapper.get("pre").element.textContent).toBe(serverManifest.system_prompt);
    await button("View YAML").trigger("click");
    await flushPromises();
    expect(wrapper.get("textarea").element).toHaveProperty("readOnly", true);
  });
});

describe("new Agent Manifest validation", () => {
  it.each([true, false])("preflights creation before writing (valid=%s)", async (valid) => {
    const { client } = setupClient();
    client.validateManifest.mockResolvedValue({
      success: true,
      data: { valid, errors: valid ? [] : ["Manifest validation error"] },
    });
    wrapper = mount(NewAgentManifestDialog, { attachTo: document.body });
    await wrapper.get('input[placeholder="e.g. ghrah.examples"]').setValue("audit");
    await wrapper.get('input[placeholder="e.g. designer"]').setValue("helper");
    await wrapper.get('input[placeholder="e.g. gpt-4o-mini"]').setValue("test-model");
    await wrapper.get("form").trigger("submit");
    await flushPromises();
    expect(client.validateManifest).toHaveBeenCalledWith(
      expect.stringContaining("manifest: agent"),
      "agent",
    );
    if (valid) {
      expect(client.putAgent).toHaveBeenCalledWith(
        "audit.helper",
        client.validateManifest.mock.calls[0][0],
        false,
        undefined,
      );
      expect(client.validateManifest.mock.invocationCallOrder[0]).toBeLessThan(
        client.putAgent.mock.invocationCallOrder[0],
      );
      expect(wrapper.emitted("close")).toHaveLength(1);
    } else {
      expect(client.putAgent).not.toHaveBeenCalled();
      expect(wrapper.get('[role="alert"]').text()).toBe("Manifest validation error");
      expect(wrapper.emitted("close")).toBeUndefined();
    }
  });
});
