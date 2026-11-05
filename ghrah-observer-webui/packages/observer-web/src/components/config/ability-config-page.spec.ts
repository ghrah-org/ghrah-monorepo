// @vitest-environment happy-dom

import type { CommandResultPayload } from "@ghrah/protocol";
import { flushPromises, mount, type VueWrapper } from "@vue/test-utils";
import { createPinia, setActivePinia } from "pinia";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import { nextTick } from "vue";
import ConfirmDialog from "@/components/ui/confirm-dialog.vue";
import { useObserver } from "@/composables/useObserver";
import fixture from "../../../../protocol/src/__fixtures__/manifest-responses.json";
import AbilityConfigPage from "./ability-config-page.vue";
import AbilityManifestDetail from "./ability-manifest-detail.vue";
import AbilityManifestEditorDialog from "./ability-manifest-editor-dialog.vue";

vi.mock("@/composables/usePlugins", () => ({ usePlugins: vi.fn() }));

const fullName = "audit.fs.read_file";
let wrapper: VueWrapper | undefined;

function deferred() {
  let resolve!: (result: CommandResultPayload) => void;
  const promise = new Promise<CommandResultPayload>((done) => (resolve = done));
  return { promise, resolve };
}

function setupClient() {
  const observer = useObserver();
  const client = {
    listManifestAbilities: vi.fn().mockResolvedValue({ success: true, data: fixture.ability_list }),
    getAbility: vi.fn().mockResolvedValue({ success: true, data: fixture.ability }),
    deleteAbility: vi.fn().mockResolvedValue({ success: false, error: "File is in use" }),
  };
  observer.client.value = client as unknown as NonNullable<typeof observer.client.value>;
  observer.connection.setConnected();
  return { observer, client };
}

async function open() {
  wrapper = mount(AbilityConfigPage, {
    global: {
      stubs: {
        AbilityManifestEditorDialog: {
          props: ["fullName", "initialMode"],
          emits: ["close", "dirtyChange", "saved"],
          template: '<div data-testid="ability-editor" />',
        },
        ConfirmDialog: {
          props: ["title", "message", "confirmLabel"],
          emits: ["confirm", "cancel"],
          template: '<div data-testid="delete-confirm" />',
        },
      },
    },
  });
  await flushPromises();
  return wrapper;
}

function button(text: string) {
  const found = wrapper!.findAll("button").find((item) => item.text() === text);
  if (!found) throw new Error(`Missing button: ${text}`);
  return found;
}

async function select() {
  await wrapper!.get(".ability-list-item").trigger("click");
  await flushPromises();
}

beforeEach(() => {
  setActivePinia(createPinia());
  useObserver().client.value = null;
  useObserver().error.value = null;
});

afterEach(() => {
  wrapper?.unmount();
  wrapper = undefined;
  useObserver().client.value = null;
});

describe("Ability configuration page", () => {
  it("reloads a populated cache on mount and separates file errors from valid summaries", async () => {
    const { observer, client } = setupClient();
    observer.manifests.setAbilities([
      { ...fixture.ability_list.abilities[0], title: "Stale title" },
    ]);
    client.listManifestAbilities.mockResolvedValue({
      success: true,
      data: {
        ...fixture.ability_list,
        errors: [{ full_name: "custom.broken", error: "Invalid YAML" }],
      },
    });
    await open();
    expect(client.listManifestAbilities).toHaveBeenCalledOnce();
    expect(wrapper!.text()).toContain("Read files");
    expect(wrapper!.text()).not.toContain("Stale title");
    expect(wrapper!.get('[role="alert"]').text()).toContain("custom.broken: Invalid YAML");
    expect(wrapper!.findAll(".ability-list-item")).toHaveLength(1);
    expect(wrapper!.text()).toContain("legacy");
    expect(wrapper!.text()).toContain("Running Agents keep their current configuration");
  });

  it("reads authoritative full details rather than rendering the cached summary", async () => {
    const { client } = setupClient();
    const manifest = structuredClone(fixture.ability.manifest);
    manifest.metadata.description = "Complete server description ".repeat(100);
    manifest.metadata.icon = "custom.icon";
    manifest.metadata.since = "1.2.3";
    manifest.metadata.deprecated = true;
    manifest.implementation.source = null;
    const data = {
      ...fixture.ability,
      manifest: {
        ...manifest,
        tool: { name: "inspect", strict: true, parameters: { path: { type: "string" } } },
        hooks: { pre_execute: [{ type: "python", handler: "check", source: "custom.py" }] },
        future: { preserve: true },
      },
    };
    client.getAbility.mockResolvedValue({ success: true, data });
    await open();
    await select();
    expect(client.getAbility).toHaveBeenCalledWith(fullName);
    for (const [key, value] of Object.entries({
      metadata: data.manifest.metadata,
      tool: data.manifest.tool,
      implementation: data.manifest.implementation,
      hooks: data.manifest.hooks,
      permissions: data.manifest.metadata.permissions,
    })) {
      expect(wrapper!.get(`[data-section="${key}"] pre`).text()).toBe(
        JSON.stringify(value, null, 2),
      );
    }
    expect(wrapper!.get("details pre").text()).toContain('"preserve": true');
  });

  it("ignores an older detail response after selecting another manifest", async () => {
    const { client } = setupClient();
    const old = deferred();
    const second = {
      ...fixture.ability,
      manifest: {
        ...fixture.ability.manifest,
        metadata: { ...fixture.ability.manifest.metadata, name: "other", title: "Other" },
      },
    };
    client.listManifestAbilities.mockResolvedValue({
      success: true,
      data: {
        abilities: [
          ...fixture.ability_list.abilities,
          { ...fixture.ability_list.abilities[0], full_name: "audit.fs.other", name: "other" },
        ],
        errors: [],
      },
    });
    client.getAbility
      .mockReturnValueOnce(old.promise)
      .mockResolvedValueOnce({ success: true, data: second });
    await open();
    await wrapper!.findAll(".ability-list-item")[0].trigger("click");
    await wrapper!.findAll(".ability-list-item")[1].trigger("click");
    await flushPromises();
    expect(wrapper!.getComponent(AbilityManifestDetail).props("detail").full_name).toBe(
      "audit.fs.other",
    );
    old.resolve({ request_id: "old-detail", success: true, data: fixture.ability });
    await flushPromises();
    expect(wrapper!.getComponent(AbilityManifestDetail).props("detail").full_name).toBe(
      "audit.fs.other",
    );
  });

  it.each([fixture.agent, { ...fixture.ability, source_hash: undefined }])(
    "rejects a mismatched or incomplete detail response",
    async (data) => {
      const { client } = setupClient();
      client.getAbility.mockResolvedValue({ success: true, data });
      await open();
      await select();
      expect(wrapper!.findComponent(AbilityManifestDetail).exists()).toBe(false);
      expect(wrapper!.get('[role="alert"]').text()).toContain(
        "The server returned an invalid response.",
      );
    },
  );

  it("refreshes the selected detail on explicit refresh and reconnect", async () => {
    const { client, observer } = setupClient();
    await open();
    await select();
    await button("Refresh").trigger("click");
    await flushPromises();
    expect(client.listManifestAbilities).toHaveBeenCalledTimes(2);
    expect(client.getAbility).toHaveBeenCalledTimes(2);
    observer.connection.setDisconnected();
    await nextTick();
    expect(wrapper!.findComponent(AbilityManifestDetail).exists()).toBe(false);
    observer.connection.setConnected();
    await flushPromises();
    expect(client.listManifestAbilities).toHaveBeenCalledTimes(3);
    expect(client.getAbility).toHaveBeenCalledTimes(3);
  });

  it("opens view/edit modes, forwards dirty state and mounts creation without a name", async () => {
    setupClient();
    await open();
    await select();
    await button("View YAML").trigger("click");
    let editor = wrapper!.getComponent(AbilityManifestEditorDialog);
    expect(editor.props()).toMatchObject({ fullName, initialMode: "view" });
    editor.vm.$emit("dirtyChange", true);
    expect(wrapper!.emitted("dirtyChange")?.at(-1)).toEqual([true]);
    editor.vm.$emit("close");
    await nextTick();
    expect(wrapper!.emitted("dirtyChange")?.at(-1)).toEqual([false]);
    await button("Edit YAML").trigger("click");
    editor = wrapper!.getComponent(AbilityManifestEditorDialog);
    expect(editor.props("initialMode")).toBe("edit");
    editor.vm.$emit("close");
    await nextTick();
    await button("+ New Ability Manifest").trigger("click");
    editor = wrapper!.getComponent(AbilityManifestEditorDialog);
    expect(editor.props("fullName")).toBeUndefined();
  });

  it("refreshes on saved without replacing the mounted creation editor", async () => {
    const { client } = setupClient();
    await open();
    await button("+ New Ability Manifest").trigger("click");
    const editor = wrapper!.getComponent(AbilityManifestEditorDialog).vm;
    editor.$emit("saved", fullName);
    await flushPromises();
    expect(wrapper!.getComponent(AbilityManifestEditorDialog).vm).toBe(editor);
    expect(client.listManifestAbilities).toHaveBeenCalledTimes(2);
    expect(client.getAbility).toHaveBeenCalledWith(fullName);
    expect(wrapper!.getComponent(AbilityManifestDetail).props("detail").full_name).toBe(fullName);
  });

  it("confirms deletion and keeps the selection and file on failure", async () => {
    const { observer, client } = setupClient();
    await open();
    await select();
    await button("Delete file").trigger("click");
    expect(client.deleteAbility).not.toHaveBeenCalled();
    const dialog = wrapper!.getComponent(ConfirmDialog);
    expect(dialog.props("message")).toContain(fullName);
    dialog.vm.$emit("confirm");
    await flushPromises();
    expect(client.deleteAbility).toHaveBeenCalledWith(fullName);
    expect(wrapper!.get('[role="alert"]').text()).toBe("File is in use");
    expect(observer.manifests.abilityList).toHaveLength(1);
    expect(wrapper!.get(".ability-list-item").attributes("aria-pressed")).toBe("true");
    expect(wrapper!.findComponent(AbilityManifestDetail).exists()).toBe(true);
    expect(client.listManifestAbilities).toHaveBeenCalledOnce();
  });

  it("refreshes from the server after successful deletion", async () => {
    const { client } = setupClient();
    await open();
    await select();
    client.deleteAbility.mockResolvedValue({ success: true });
    client.listManifestAbilities.mockResolvedValue({
      success: true,
      data: { abilities: [], errors: [] },
    });
    await button("Delete file").trigger("click");
    wrapper!.getComponent(ConfirmDialog).vm.$emit("confirm");
    await flushPromises();
    expect(client.listManifestAbilities).toHaveBeenCalledTimes(2);
    expect(wrapper!.findComponent(AbilityManifestDetail).exists()).toBe(false);
    expect(wrapper!.text()).toContain("No Ability Manifest files found.");
  });

  it("shows list failures without reporting an empty success state", async () => {
    const { client } = setupClient();
    client.listManifestAbilities.mockResolvedValue({ success: false, error: "Permission denied" });
    await open();
    expect(wrapper!.get('[role="alert"]').text()).toBe("Permission denied");
    expect(wrapper!.text()).not.toContain("No Ability Manifest files found.");
  });

  it("ignores detail completion after unmount", async () => {
    const { client } = setupClient();
    const pending = deferred();
    client.getAbility.mockReturnValue(pending.promise);
    await open();
    await wrapper!.get(".ability-list-item").trigger("click");
    const instance = wrapper!;
    instance.unmount();
    wrapper = undefined;
    pending.resolve({ request_id: "unmounted-detail", success: true, data: fixture.ability });
    await flushPromises();
    expect(instance.emitted("dirtyChange")).toBeUndefined();
  });
});
