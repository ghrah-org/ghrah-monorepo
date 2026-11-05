// SPDX-FileCopyrightText: 2026 chenxya <chenxya@ghrah.org>
//
// SPDX-License-Identifier: Apache-2.0

// @vitest-environment happy-dom

import { useTasksStore } from "@ghrah/observer-core";
import { mount } from "@vue/test-utils";
import { createPinia, setActivePinia } from "pinia";
import { beforeEach, describe, expect, it, vi } from "vitest";
import { defineComponent, h, nextTick, ref } from "vue";

const manifestError = ref<string | null>(null);
const loadResults = ref<Array<{ pluginId: string; ok: boolean }>>([]);
let badgeComponent: ReturnType<typeof defineComponent> | null = null;

vi.mock("@/composables/usePlugins", () => ({
  usePlugins: () => ({
    loadManifest: vi.fn(),
    tsHalfProvider: () => [],
    resolveBadge: (kind: string) => (kind === "git_commit" ? badgeComponent : null),
    manifestError,
    loadResults,
    negotiation: ref(null),
  }),
}));

import TaskAttributionPage from "./task-attribution-page.vue";

const pluginBadge = defineComponent({
  props: { evidence: { type: Object, required: true } },
  setup(props) {
    return () => h("span", { class: "plugin-badge-hit" }, `sha:${props.evidence.ref}`);
  },
});

function seedClaim(overrides: Record<string, unknown> = {}) {
  const tasks = useTasksStore();
  tasks.onClaimEvent("task_delivered", {
    task: {
      task_id: "t1",
      project_id: "p1",
      title: "ship it",
      description: "",
      agent_id: null,
      agent_name: null,
      status: "delivered",
      priority: "normal",
      parent_id: null,
      dependencies: [],
      result: null,
      error: null,
      created_at: "",
      updated_at: "",
      started_at: null,
      completed_at: null,
      metadata: {},
      verification: null,
    },
    claim: {
      claim_id: "c1",
      task_id: "t1",
      claimant_type: "agent",
      claimant_id: "a1",
      claimant_name: "agent-1",
      note: null,
      evidence: [
        {
          evidence_id: "e1",
          kind: "git_commit",
          ref: "ghrah@abc1234",
          digest: null,
          payload: { sha: "abc1234" },
          created_by: "a1",
          created_at: "",
        },
        {
          evidence_id: "e2",
          kind: "unknown_kind",
          ref: "lint-report-42",
          digest: null,
          payload: {},
          created_by: "a1",
          created_at: "",
        },
      ],
      state: "submitted",
      checks: [{ checker: "commit-exists", passed: true, detail: null }],
      verdict_by: null,
      verdict_at: null,
      verdict_reason: null,
      provenance: null,
      created_at: "",
    },
    previous_status: null,
    reason: null,
    ...overrides,
  });
}

describe("TaskAttributionPage", () => {
  beforeEach(() => {
    manifestError.value = null;
    loadResults.value = [];
    badgeComponent = null;
  });

  function mountPage() {
    const pinia = createPinia();
    setActivePinia(pinia);
    return mount(TaskAttributionPage, { global: { plugins: [pinia] } });
  }

  it("renders plugin badge when the renderer is registered for the evidence kind", async () => {
    badgeComponent = pluginBadge;
    const wrapper = mountPage();
    seedClaim();
    await nextTick();
    const hit = wrapper.find(".plugin-badge-hit");
    expect(hit.exists()).toBe(true);
    expect(hit.text()).toContain("ghrah@abc1234");
  });

  it("falls back to raw kind + ref when no renderer matches", async () => {
    badgeComponent = null;
    const wrapper = mountPage();
    seedClaim();
    await nextTick();
    const raws = wrapper.findAll(".evidence-badge-raw");
    expect(raws.length).toBe(2);
    expect(raws[1].text()).toContain("unknown_kind");
    expect(raws[1].text()).toContain("lint-report-42");
  });

  it("shows empty placeholder when no tasks exist", () => {
    const wrapper = mountPage();
    expect(wrapper.text()).toContain("No tasks yet.");
  });
});
