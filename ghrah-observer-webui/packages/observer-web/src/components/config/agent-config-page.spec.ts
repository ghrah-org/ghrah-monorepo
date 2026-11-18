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

const hoisted = vi.hoisted(() => {
  const calls: { method: string; path: string; body?: unknown }[] = [];

  class FakeHttpClient {
    constructor(_options: unknown) {}

    async get(path: string): Promise<unknown> {
      calls.push({ method: "GET", path });
      if (path === "/api/v1/projects") {
        return {
          projects: [{ project_id: "p1", name: "default", version: 7, archived_at: null }],
        };
      }
      throw new Error(`unexpected GET ${path}`);
    }

    async post(path: string, body: unknown): Promise<unknown> {
      calls.push({ method: "POST", path, body });
      return {};
    }
  }

  return { calls, FakeHttpClient };
});

const httpCalls = hoisted.calls;

vi.mock("@ghrah/observer-core", async (importOriginal) => {
  const actual = await importOriginal<typeof import("@ghrah/observer-core")>();
  return {
    ...actual,
    HttpClient: hoisted.FakeHttpClient,
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
  metadata: {
    namespace: "writing",
    name: "weekly",
    title: "周报助手",
    description: "",
    tags: ["demo"],
    icon: "",
    deprecated: false,
  },
  created_at: "2026-11-18T00:00:00Z",
  updated_at: "2026-11-18T00:00:00Z",
  archived_at: null,
  reference_count: 3,
};

const DETAIL = {
  format: "ghrah.agent-templates.v1",
  template_id: "t1",
  revision: 2,
  is_latest: true,
  metadata: SUMMARY.metadata,
  model: { endpoint_name: "glm-5.3", model_overrides: null },
  prompt: { system_prompt: "先列事实，再给结论。" },
  context_policy: {
    max_iterations: 6,
    communication_timeout: 300,
    window: null,
    overrides: null,
  },
  ability_request: {
    surface: { mode: "auto", static: [], max_static: null },
    packages: [],
    mcp_refs: [],
  },
  permission_request: null,
  path_request: [],
  created_at: SUMMARY.created_at,
  updated_at: SUMMARY.updated_at,
  archived_at: null,
  reference_count: 3,
};

function mountPage() {
  const pinia = createPinia();
  setActivePinia(pinia);
  return mount(AgentConfigPage, { global: { plugins: [pinia] } });
}

function buttonByText(
  wrapper: ReturnType<typeof mountPage>,
  text: string,
): ReturnType<typeof wrapper.findAll>[number] | undefined {
  return wrapper.findAll("button").find((button) => button.text().includes(text));
}

describe("AgentConfigPage (templates)", () => {
  beforeEach(() => {
    vi.clearAllMocks();
    httpCalls.length = 0;
    api.list.mockResolvedValue({ templates: [] });
    api.get.mockResolvedValue(DETAIL);
    api.versions.mockResolvedValue({ template_id: "t1", revisions: [] });
  });

  it("shows the empty state when the library has no templates", async () => {
    const wrapper = mountPage();
    await flushPromises();
    expect(api.list).toHaveBeenCalled();
    expect(wrapper.text()).toContain("No templates yet");
  });

  it("renders a card per template with identity, version and references", async () => {
    api.list.mockResolvedValue({ templates: [SUMMARY] });
    const wrapper = mountPage();
    await flushPromises();

    expect(wrapper.text()).toContain("周报助手");
    expect(wrapper.text()).toContain("writing.weekly");
    expect(wrapper.text()).toContain("v2");
    expect(wrapper.text()).toContain("latest");
    expect(wrapper.text()).toContain("Agents: 3");
  });

  it("filters cards by the search box", async () => {
    api.list.mockResolvedValue({ templates: [SUMMARY] });
    const wrapper = mountPage();
    await flushPromises();

    await wrapper.find("input[type=search]").setValue("nothing");
    expect(wrapper.text()).toContain("No template matches this search.");
  });

  it("asks the server for history when the toggle is switched on", async () => {
    const wrapper = mountPage();
    await flushPromises();

    const history = wrapper.findAll("input[type=checkbox]")[1];
    await history!.setValue(true);
    await flushPromises();

    expect(api.list).toHaveBeenLastCalledWith(expect.objectContaining({ includeHistory: true }));
  });

  it("creates an agent in a project from a card", async () => {
    api.list.mockResolvedValue({ templates: [SUMMARY] });
    const wrapper = mountPage();
    await flushPromises();

    await buttonByText(wrapper, "Create agent")!.trigger("click");
    await flushPromises();

    expect(wrapper.text()).toContain("Create agent from template");

    const forms = wrapper.findAll("form");
    await forms[forms.length - 1]!.trigger("submit");
    await flushPromises();

    const post = httpCalls.find((call) => call.method === "POST");
    expect(post?.path).toBe("/api/v1/projects/p1/agents");
    expect(post?.body).toMatchObject({
      expected_revision: 7,
      template_id: "t1",
      revision: 2,
      desired_state: "running",
    });
  });
});
