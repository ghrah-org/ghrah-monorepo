// SPDX-FileCopyrightText: 2026 chenxya <chenxya@ghrah.org>
//
// SPDX-License-Identifier: Apache-2.0

// @vitest-environment happy-dom

import { useProjectsStore } from "@ghrah/observer-core";
import type { ProjectInfoPayload } from "@ghrah/protocol";
import { mount } from "@vue/test-utils";
import { createPinia, setActivePinia } from "pinia";
import { beforeEach, describe, expect, it, vi } from "vitest";
import { ref } from "vue";

const switchProjectMock = vi.fn();
const createProjectMock = vi.fn();
const errorRef = ref<string | null>(null);
vi.mock("@/composables/useObserver", () => ({
  useObserver: () => ({
    switchProject: switchProjectMock,
    createProject: createProjectMock,
    error: errorRef,
  }),
}));

import ProjectSelector from "./project-selector.vue";

function project(id: string, name: string): ProjectInfoPayload {
  return { project_id: id, name } as ProjectInfoPayload;
}

describe("ProjectSelector", () => {
  beforeEach(() => {
    setActivePinia(createPinia());
    switchProjectMock.mockReset();
    createProjectMock.mockReset();
    errorRef.value = null;
  });

  it("shows empty state when no projects", () => {
    const wrapper = mount(ProjectSelector);
    expect(wrapper.text()).toContain("No projects");
  });

  it("lists projects and emits switchProject on click", async () => {
    const projects = useProjectsStore();
    projects.setProjectsFromList([project("p1", "alpha"), project("p2", "beta")]);
    const wrapper = mount(ProjectSelector);
    await wrapper.vm.$nextTick();
    const items = wrapper.findAll("li");
    expect(items).toHaveLength(2);
    expect(wrapper.text()).toContain("alpha");
    expect(wrapper.text()).toContain("beta");
    await items[1].trigger("click");
    expect(switchProjectMock).toHaveBeenCalledWith("p2");
  });

  it("highlights the active project", async () => {
    const projects = useProjectsStore();
    projects.setProjectsFromList([project("p1", "alpha"), project("p2", "beta")]);
    projects.setActiveProject("p1");
    const wrapper = mount(ProjectSelector);
    await wrapper.vm.$nextTick();
    const items = wrapper.findAll("li");
    // 选中态由 .project-item.active 的 ::before/::after 指示条表达，不再铺背景色
    expect(items[0].classes()).toContain("active");
    expect(items[1].classes()).not.toContain("active");
  });

  it("creates a project from inline form and selects it", async () => {
    const wrapper = mount(ProjectSelector);
    await wrapper.find('button[title="New project"]').trigger("click");
    const input = wrapper.find('input[placeholder="Project name"]');
    expect(input.exists()).toBe(true);
    await input.setValue("demo-project");
    await wrapper.find('textarea[placeholder="What is this project for?"]').setValue("Demo app");
    await wrapper
      .find('input[placeholder="Auto-generated under ~/.ghrah/projects"]')
      .setValue("/private/demo");
    await wrapper.find("button.project-workspace-add").trigger("click");
    await wrapper.find('input[aria-label="Workspace 1 path"]').setValue("/work/demo");
    createProjectMock.mockResolvedValue({
      success: true,
      data: { project: { project_id: "p9", name: "demo-project" } },
    });
    await wrapper.find("form").trigger("submit.prevent");
    expect(createProjectMock).toHaveBeenCalledWith("demo-project", {
      description: "Demo app",
      projectRootLocator: "/private/demo",
      writableWorkspaces: [
        {
          locator: "/work/demo",
          name: "default",
          role: "default",
          defaultForAgents: true,
        },
      ],
    });
    expect(switchProjectMock).toHaveBeenCalledWith("p9");
    // 成功后表单收起
    expect(wrapper.find('input[placeholder="Project name"]').exists()).toBe(false);
  });

  it("shows backend error when creation fails", async () => {
    const wrapper = mount(ProjectSelector);
    await wrapper.find('button[title="New project"]').trigger("click");
    await wrapper.find('input[placeholder="Project name"]').setValue("dup");
    createProjectMock.mockResolvedValue({
      success: false,
      error: "project already exists",
    });
    await wrapper.find("form").trigger("submit.prevent");
    expect(errorRef.value).toBe("project already exists");
    // 失败时表单保留（用户可重试/取消）
    expect(wrapper.find('input[placeholder="Project name"]').exists()).toBe(true);
  });

  it("creates a project with no writable workspaces", async () => {
    const wrapper = mount(ProjectSelector);
    await wrapper.find('button[title="New project"]').trigger("click");
    await wrapper.find('input[placeholder="Project name"]').setValue("private-project");
    createProjectMock.mockResolvedValue({
      success: true,
      data: { project: { project_id: "p10", name: "private-project" } },
    });
    await wrapper.find("form").trigger("submit.prevent");
    expect(createProjectMock).toHaveBeenCalledWith("private-project", {
      description: "",
      projectRootLocator: undefined,
      writableWorkspaces: [],
    });
  });

  it("cancels creation via esc", async () => {
    const wrapper = mount(ProjectSelector);
    await wrapper.find('button[title="New project"]').trigger("click");
    await wrapper.find('input[placeholder="Project name"]').trigger("keydown.esc");
    expect(wrapper.find('input[placeholder="Project name"]').exists()).toBe(false);
    expect(createProjectMock).not.toHaveBeenCalled();
  });
});
