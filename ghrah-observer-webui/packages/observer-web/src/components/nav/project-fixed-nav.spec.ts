// SPDX-FileCopyrightText: 2026 chenxya <chenxya@ghrah.org>
//
// SPDX-License-Identifier: Apache-2.0

// @vitest-environment happy-dom

import { useProjectsStore } from "@ghrah/observer-core";
import { mount } from "@vue/test-utils";
import { createPinia, setActivePinia } from "pinia";
import { beforeEach, describe, expect, it } from "vitest";
import ProjectFixedNav from "./project-fixed-nav.vue";

describe("ProjectFixedNav", () => {
  beforeEach(() => setActivePinia(createPinia()));

  it("disables all entries without an active project", () => {
    const wrapper = mount(ProjectFixedNav);
    expect(wrapper.text()).toContain("Select a project to open project views");
    expect(
      wrapper.findAll("button").every((button) => button.attributes("disabled") !== undefined),
    ).toBe(true);
  });

  it("emits project-scoped fixed views", async () => {
    const projects = useProjectsStore();
    projects.setProjectsFromList([
      { project_id: "p1", name: "Project One", archived_at: null } as never,
    ]);
    projects.setActiveProject("p1");
    const wrapper = mount(ProjectFixedNav);

    for (const button of wrapper.findAll("button")) await button.trigger("click");

    expect(wrapper.emitted("openProjectView")).toEqual([
      [{ kind: "changes", projectId: "p1" }],
      [{ kind: "agents", projectId: "p1" }],
      [{ kind: "assembly", projectId: "p1" }],
      [{ kind: "project", projectId: "p1" }],
    ]);
  });
});
