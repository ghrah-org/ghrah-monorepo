// SPDX-FileCopyrightText: 2026 chenxya <chenxya@ghrah.org>
//
// SPDX-License-Identifier: Apache-2.0

// @vitest-environment happy-dom

import { flushPromises, mount } from "@vue/test-utils";
import { createPinia, setActivePinia } from "pinia";
import { beforeEach, describe, expect, it, vi } from "vitest";

const api = {
  list: vi.fn(),
  get: vi.fn(),
  create: vi.fn(),
  update: vi.fn(),
  versions: vi.fn(),
  references: vi.fn(),
  archive: vi.fn(),
};

vi.mock("@ghrah/observer-core", async (importOriginal) => {
  const actual = await importOriginal<typeof import("@ghrah/observer-core")>();
  return {
    ...actual,
    HttpClient: class {
      constructor(_options: unknown) {}
    },
    useConnectionStore: () => ({ httpBaseUrl: "http://127.0.0.1:4112", apiToken: undefined }),
    createAgentTemplatesApi: () => api,
  };
});

import AgentConfigPage from "./agent-config-page.vue";

const SUMMARY = {
  format: "ghrah.agent-templates.summary.v1",
  template_id: "t1",
  revision: 2,
  is_latest: true,
  metadata: { namespace: "writing", name: "weekly", title: "周报助手" },
  created_at: "2026-11-18T00:00:00Z",
  updated_at: "2026-11-18T00:00:00Z",
  archived_at: null,
  reference_count: 3,
};

function mountPage() {
  const pinia = createPinia();
  setActivePinia(pinia);
  return mount(AgentConfigPage, { global: { plugins: [pinia] } });
}

describe("AgentConfigPage (templates)", () => {
  beforeEach(() => {
    vi.clearAllMocks();
    api.list.mockResolvedValue({ templates: [] });
    api.versions.mockResolvedValue({ template_id: "t1", revisions: [] });
  });

  it("shows the empty state when the library has no templates", async () => {
    const wrapper = mountPage();
    await flushPromises();
    expect(api.list).toHaveBeenCalled();
    expect(wrapper.text()).toContain("No templates yet");
  });

  it("renders rows returned by the REST list", async () => {
    api.list.mockResolvedValue({ templates: [SUMMARY] });
    const wrapper = mountPage();
    await flushPromises();

    expect(wrapper.text()).toContain("周报助手");
    expect(wrapper.text()).toContain("v2");
    expect(wrapper.text()).toContain("latest");
    expect(wrapper.text()).toContain("3");
  });

  it("asks for history when the toggle is switched on", async () => {
    const wrapper = mountPage();
    await flushPromises();

    await wrapper.findAll("input[type=checkbox]")[1]!.setValue(true);
    await flushPromises();

    expect(api.list).toHaveBeenLastCalledWith(expect.objectContaining({ includeHistory: true }));
  });

  it("opens the create dialog from the header button", async () => {
    const wrapper = mountPage();
    await flushPromises();

    const create = wrapper
      .findAll("button")
      .find((button) => button.text().includes("New template"));
    expect(create).toBeDefined();
    await create!.trigger("click");

    expect(wrapper.text()).toContain("New agent template");
  });
});
