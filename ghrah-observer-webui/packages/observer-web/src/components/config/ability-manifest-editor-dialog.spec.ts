// @vitest-environment happy-dom

import { createPinia, setActivePinia } from "pinia";
import { flushPromises, mount, type VueWrapper } from "@vue/test-utils";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import { useObserver } from "@/composables/useObserver";
import fixture from "../../../../protocol/src/__fixtures__/manifest-responses.json";
import AbilityManifestEditorDialog from "./ability-manifest-editor-dialog.vue";

vi.mock("@/composables/usePlugins", () => ({ usePlugins: vi.fn() }));

const fullName = "audit.fs.read_file";
let wrapper: VueWrapper | undefined;
let serverSource: string;
let serverSourceHash: string;
let serverManifest: typeof fixture.ability.manifest;

function setupClient() {
  const observer = useObserver();
  const client = {
    getAbility: vi.fn().mockImplementation(async () => ({
      success: true,
      data: { manifest: serverManifest, source: serverSource, source_hash: serverSourceHash },
    })),
    putAbility: vi.fn().mockImplementation(async (_name: string, source: string) => {
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
  wrapper = mount(AbilityManifestEditorDialog, {
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
  serverSource = fixture.ability.source;
  serverSourceHash = fixture.ability.source_hash;
  serverManifest = structuredClone(fixture.ability.manifest);
  const observer = useObserver();
  observer.client.value = null;
  observer.error.value = null;
});
afterEach(() => {
  wrapper?.unmount();
  wrapper = undefined;
  useObserver().client.value = null;
});

describe("Ability Manifest editor", () => {
  it("reads the complete original source, including comments, in view mode", async () => {
    const { client } = setupClient();
    serverSource += "# " + "long prompt content ".repeat(80);
    await open("view");
    const field = wrapper!.get("textarea").element as HTMLTextAreaElement;
    expect(field.value).toBe(serverSource);
    expect(field.readOnly).toBe(true);
    expect(client.getAbility).toHaveBeenCalledWith(fullName);
    await button("Edit YAML").trigger("click");
    expect(field.readOnly).toBe(false);
    expect(client.putAbility).not.toHaveBeenCalled();
  });

  it("validates the exact draft, saves it and reads the authority back", async () => {
    const { client, observer } = setupClient();
    await open();
    const draft = serverSource + "\n# Keep comments and untouched settings.\n";
    await wrapper!.get("textarea").setValue(draft);
    await button("Validate and save").trigger("click");
    await flushPromises();
    expect(client.validateManifest).toHaveBeenCalledWith(draft, "ability");
    expect(client.putAbility).toHaveBeenCalledWith(
      fullName,
      draft,
      true,
      fixture.ability.source_hash,
    );
    expect(client.getAbility).toHaveBeenCalledTimes(2);
    expect(client.validateManifest.mock.invocationCallOrder[0]).toBeLessThan(
      client.putAbility.mock.invocationCallOrder[0],
    );
    expect(wrapper!.get("textarea").element).toHaveProperty("value", draft);
    expect(wrapper!.text()).toContain("Saved and verified from the server.");
    expect(observer.manifests.abilityDetails.get(fullName)?.source).toBe(draft);
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
    expect(client.putAbility).not.toHaveBeenCalled();
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
    expect(client.putAbility).not.toHaveBeenCalled();
  });

  it("preserves the draft on an explicit save rejection", async () => {
    const { client } = setupClient();
    client.putAbility.mockResolvedValue({
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
    client.putAbility.mockResolvedValue({
      success: false,
      error: "Manifest changed on the server; reload the current source before saving",
      error_detail: `code=manifest_conflict; expected_source_hash=${fixture.ability.source_hash}; current_source_hash=sha256:different`,
    });
    await open();
    const draft = serverSource + "# Concurrent edit\n";
    await wrapper!.get("textarea").setValue(draft);
    await button("Validate and save").trigger("click");
    await flushPromises();
    expect(client.putAbility).toHaveBeenCalledWith(
      fullName,
      draft,
      true,
      fixture.ability.source_hash,
    );
    expect(wrapper!.get('[role="alert"]').text()).toContain("changed on the server");
    expect(button("Validate and save").attributes("disabled")).toBeDefined();
    expect(wrapper!.get("textarea").element).toHaveProperty("value", draft);
    expect(client.getAbility).toHaveBeenCalledTimes(1);
  });

  it("retries read-back without writing twice when the save was accepted", async () => {
    const { client } = setupClient();
    await open();
    client.getAbility.mockRejectedValueOnce(new Error("Connection lost"));
    await wrapper!.get("textarea").setValue(serverSource + "# Draft\n");
    await button("Validate and save").trigger("click");
    await flushPromises();
    expect(wrapper!.text()).toContain("reading it back failed");
    await button("Check saved file").trigger("click");
    await flushPromises();
    expect(client.putAbility).toHaveBeenCalledTimes(1);
    expect(wrapper!.text()).toContain("Saved and verified from the server.");
  });

  it("keeps the draft if the server differs after an uncertain save", async () => {
    const { client } = setupClient();
    client.putAbility.mockRejectedValueOnce(new Error("Connection lost"));
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
    expect(client.putAbility).toHaveBeenCalledTimes(1);
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
    client.getAbility.mockRejectedValueOnce(new Error("Read failed"));
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
    client.getAbility.mockReturnValueOnce(
      new Promise((done) => {
        resolve = done;
      }),
    );
    await open();
    const instance = wrapper!;
    instance.unmount();
    wrapper = undefined;
    resolve({ success: true, data: fixture.ability });
    await flushPromises();
    expect(instance.emitted("dirtyChange")).toBeUndefined();
    expect(instance.emitted("close")).toBeUndefined();
  });
});

describe("Ability creation", () => {
  it("validates before creating without overwrite and reads back the new file", async () => {
    const { client } = setupClient();
    wrapper = mount(AbilityManifestEditorDialog, { attachTo: document.body });
    await wrapper.get("textarea").setValue(fixture.ability.source);
    await button("Validate and save").trigger("click");
    await flushPromises();
    expect(client.validateManifest).toHaveBeenCalledWith(fixture.ability.source, "ability");
    expect(client.putAbility).toHaveBeenCalledWith(
      fullName,
      fixture.ability.source,
      false,
      undefined,
    );
    expect(client.getAbility).toHaveBeenCalledTimes(1);
    expect(wrapper.text()).toContain("Saved and verified from the server.");
    expect(wrapper.emitted("saved")).toEqual([[fullName]]);
  });
  it("retains a duplicate identity draft without overwriting", async () => {
    const { client } = setupClient();
    client.putAbility.mockResolvedValue({
      success: false,
      error: "Ability manifest already exists",
    });
    wrapper = mount(AbilityManifestEditorDialog);
    await wrapper.get("textarea").setValue(fixture.ability.source);
    await button("Validate and save").trigger("click");
    await flushPromises();
    expect(wrapper.text()).toContain("already exists");
    expect(wrapper.get("textarea").element).toHaveProperty("value", fixture.ability.source);
    expect(client.getAbility).not.toHaveBeenCalled();
  });
  it("refuses an identity change before sending a write", async () => {
    const { client } = setupClient();
    await open();
    await wrapper!.get("textarea").setValue(serverSource.replace('"read_file"', '"renamed"'));
    await button("Validate and save").trigger("click");
    await flushPromises();
    expect(client.putAbility).not.toHaveBeenCalled();
    expect(wrapper!.text()).toContain("Keep metadata.namespace and metadata.name unchanged");
  });
  it("requires explicit discard before reloading after a conflict", async () => {
    const { client } = setupClient();
    client.putAbility.mockResolvedValue({ success: false, error_detail: "code=manifest_conflict" });
    await open();
    const draft = serverSource + "# mine\n";
    await wrapper!.get("textarea").setValue(draft);
    await button("Validate and save").trigger("click");
    await flushPromises();
    await button("Retry loading").trigger("click");
    expect(client.getAbility).toHaveBeenCalledTimes(1);
    await button("Cancel").trigger("click");
    expect(wrapper!.get("textarea").element).toHaveProperty("value", draft);
    await button("Retry loading").trigger("click");
    await button("Discard changes").trigger("click");
    await flushPromises();
    expect(client.getAbility).toHaveBeenCalledTimes(2);
    expect(wrapper!.get("textarea").element).toHaveProperty("value", serverSource);
  });
});
