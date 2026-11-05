// SPDX-FileCopyrightText: 2026 chenxya <chenxya@ghrah.org>
//
// SPDX-License-Identifier: Apache-2.0

// @vitest-environment happy-dom

import { useChangesStore, useProjectsStore } from "@ghrah/observer-core";
import { mount } from "@vue/test-utils";
import { createPinia, setActivePinia } from "pinia";
import { beforeEach, describe, expect, it } from "vitest";
import ProjectChangesPanel from "./project-changes-panel.vue";

describe("ProjectChangesPanel", () => {
  beforeEach(() => {
    setActivePinia(createPinia());
    useProjectsStore().setProjectsFromList([
      { project_id: "p1", name: "Project One", archived_at: null } as never,
      { project_id: "p2", name: "Project Two", archived_at: null } as never,
    ]);
    useChangesStore().changes = [
      {
        projectId: "p1",
        agentId: "a1",
        agentName: "Coder",
        sessionId: "s1",
        branchId: "b1",
        nodeId: "n1",
        abilityName: "edit_file",
        filePath: "src/main.ts",
        success: true,
        result: { changed: true },
        timestamp: "2026-09-10T00:00:00Z",
      },
      {
        projectId: "p2",
        agentId: "a2",
        agentName: "Foreign",
        sessionId: "s2",
        branchId: "b2",
        nodeId: "n2",
        abilityName: "write_file",
        filePath: "foreign.ts",
        success: true,
        timestamp: "2026-09-10T00:00:00Z",
      },
    ];
  });

  it("shows only current-project changes and their full source context", async () => {
    const wrapper = mount(ProjectChangesPanel, { props: { projectId: "p1" } });

    expect(wrapper.text()).toContain("src/main.ts");
    expect(wrapper.text()).toContain("@Coder");
    expect(wrapper.text()).toContain("s1 / b1");
    expect(wrapper.text()).not.toContain("foreign.ts");

    await wrapper.find(".project-change-row button").trigger("click");
    expect(wrapper.text()).toContain('"changed": true');
  });
});
