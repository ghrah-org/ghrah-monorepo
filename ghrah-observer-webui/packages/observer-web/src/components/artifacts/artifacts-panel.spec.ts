// SPDX-FileCopyrightText: 2026 chenxya <chenxya@ghrah.org>
//
// SPDX-License-Identifier: Apache-2.0

// @vitest-environment happy-dom

import { HttpError, useProjectsStore } from "@ghrah/observer-core";
import { flushPromises, mount } from "@vue/test-utils";
import { createPinia, setActivePinia } from "pinia";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

const api = vi.hoisted(() => ({
  listWorkspaceFiles: vi.fn(),
  listArtifacts: vi.fn(),
  readWorkspaceFile: vi.fn(),
  readArtifact: vi.fn(),
  publishArtifact: vi.fn(),
}));

vi.mock("@ghrah/observer-core", async (importOriginal) => {
  const actual = await importOriginal<typeof import("@ghrah/observer-core")>();
  return { ...actual, ...api };
});

import ArtifactsPanel from "./artifacts-panel.vue";

const artifact = {
  artifact_id: "a".repeat(64),
  filename: "REQ-DRAFT-0001-requirement.svg",
  mime_type: "image/svg+xml",
  size: 2048,
  title: "Requirement draft",
  published_at: "2026-11-12T00:00:00Z",
  source: { workspace_id: "w1", path: "outputs/requirements/REQ.svg", agent_id: "a1" },
};

function project(workspaces: Array<{ workspace_id: string; role?: string | null }>) {
  const projects = useProjectsStore();
  projects.setProjectsFromList([
    {
      project_id: "p1",
      name: "Project One",
      workspaces,
      archived_at: null,
    } as never,
  ]);
  return projects;
}

function mountPanel() {
  return mount(ArtifactsPanel, { props: { projectId: "p1" } });
}

function svgBytes() {
  return {
    bytes: new TextEncoder().encode("<svg xmlns='http://www.w3.org/2000/svg'></svg>"),
    mimeType: "image/svg+xml",
  };
}

beforeEach(() => {
  setActivePinia(createPinia());
  api.listWorkspaceFiles.mockReset();
  api.listArtifacts.mockReset();
  api.readWorkspaceFile.mockReset();
  api.readArtifact.mockReset();
  api.publishArtifact.mockReset();
  api.listWorkspaceFiles.mockResolvedValue({ path: "", entries: [] });
  api.listArtifacts.mockResolvedValue([]);
  Object.defineProperty(URL, "createObjectURL", {
    configurable: true,
    value: vi.fn(() => "blob:mock"),
  });
  Object.defineProperty(URL, "revokeObjectURL", {
    configurable: true,
    value: vi.fn(),
  });
});

afterEach(() => {
  vi.restoreAllMocks();
});

describe("ArtifactsPanel", () => {
  it("shows the no-workspace empty state", async () => {
    project([]);
    const wrapper = mountPanel();
    await flushPromises();
    expect(wrapper.text()).toContain("This project has no workspaces.");
    expect(api.listWorkspaceFiles).not.toHaveBeenCalled();
  });

  it("browses directories, previews a workspace file, and enables publishing", async () => {
    project([{ workspace_id: "w1", role: "source" }]);
    api.listWorkspaceFiles
      .mockResolvedValueOnce({
        path: "",
        entries: [{ name: "outputs", path: "outputs", type: "dir" }],
      })
      .mockResolvedValueOnce({
        path: "outputs",
        entries: [
          {
            name: "REQ.svg",
            path: "outputs/REQ.svg",
            type: "file",
            size: 12,
          },
        ],
      });
    api.readWorkspaceFile.mockResolvedValue(svgBytes());

    const wrapper = mountPanel();
    await flushPromises();
    expect(api.listWorkspaceFiles).toHaveBeenCalledWith("p1", "w1", "");
    expect(wrapper.text()).toContain("outputs");

    await wrapper
      .findAll("button")
      .find((button) => button.text().includes("outputs"))!
      .trigger("click");
    await flushPromises();
    expect(api.listWorkspaceFiles).toHaveBeenLastCalledWith("p1", "w1", "outputs");

    await wrapper
      .findAll("button")
      .find((button) => button.text().includes("REQ.svg"))!
      .trigger("click");
    await flushPromises();
    expect(api.readWorkspaceFile).toHaveBeenCalledWith("p1", "w1", "outputs/REQ.svg");
    const image = wrapper.find(".artifacts-preview-media");
    expect(image.exists()).toBe(true);
    expect(image.attributes("src")).toBe("blob:mock");
    expect(wrapper.text()).toContain("REQ.svg");
  });

  it("publishes the selected file and refreshes the artifact list", async () => {
    project([{ workspace_id: "w1" }]);
    api.listWorkspaceFiles.mockResolvedValue({
      path: "",
      entries: [{ name: "REQ.svg", path: "REQ.svg", type: "file", size: 12 }],
    });
    api.readWorkspaceFile.mockResolvedValue(svgBytes());
    api.publishArtifact.mockResolvedValue(artifact);
    api.listArtifacts.mockResolvedValueOnce([]).mockResolvedValueOnce([artifact]);

    const wrapper = mountPanel();
    await flushPromises();
    await wrapper
      .findAll("button")
      .find((button) => button.text().includes("REQ.svg"))!
      .trigger("click");
    await flushPromises();

    await wrapper.find('input:not([type="file"])').setValue("需求图");
    const publishButton = wrapper
      .findAll("button")
      .find((button) => button.text().includes("Publish artifact"))!;
    expect(publishButton.attributes("disabled")).toBeUndefined();
    await publishButton.trigger("click");
    await flushPromises();

    expect(api.publishArtifact).toHaveBeenCalledWith("p1", {
      workspace_id: "w1",
      path: "REQ.svg",
      mime_type: "image/svg+xml",
      title: "需求图",
    });
    expect(wrapper.text()).toContain("Published Requirement draft.");
    expect(api.listArtifacts).toHaveBeenCalledTimes(2);
    expect(wrapper.text()).toContain("Requirement draft");
  });

  it("falls back to Agent path_grants when the project has no mounted workspaces", async () => {
    const projects = useProjectsStore();
    projects.setProjectsFromList([
      {
        project_id: "p1",
        name: "Project One",
        workspaces: [],
        agents: [
          {
            name: "agent-a",
            cluster_id: "c1",
            path_grants: [{ workspace_id: "w9", subpath: "." }],
          },
        ],
        archived_at: null,
      } as never,
    ]);
    api.listWorkspaceFiles.mockResolvedValue({ path: "", entries: [] });
    const wrapper = mountPanel();
    await flushPromises();
    expect(api.listWorkspaceFiles).toHaveBeenCalledWith("p1", "w9", "");
    expect(wrapper.text()).toContain("w9");
  });

  it("maps workspace errors to readable copy", async () => {
    project([{ workspace_id: "w1" }]);
    api.listWorkspaceFiles.mockRejectedValue(
      new HttpError({ code: "workspace_not_found", status: 404, message: "missing" }),
    );
    const wrapper = mountPanel();
    await flushPromises();
    expect(wrapper.text()).toContain("Workspace not found.");
  });

  it("lists artifacts, previews one, and shows empty state when none", async () => {
    project([{ workspace_id: "w1" }]);
    api.listArtifacts.mockResolvedValue([artifact]);
    api.readArtifact.mockResolvedValue(svgBytes());
    const wrapper = mountPanel();
    await flushPromises();
    expect(wrapper.text()).toContain("Requirement draft");
    expect(wrapper.text()).toContain("Source: w1:outputs/requirements/REQ.svg");

    await wrapper
      .findAll("button")
      .find((button) => button.text() === "Preview")!
      .trigger("click");
    await flushPromises();
    expect(api.readArtifact).toHaveBeenCalledWith("p1", artifact.artifact_id);
    expect(wrapper.find(".artifacts-preview-media").exists()).toBe(true);
    expect(wrapper.text()).toContain("Artifact");

    api.listArtifacts.mockResolvedValue([]);
    const empty = mountPanel();
    await flushPromises();
    expect(empty.text()).toContain("No artifacts published yet.");
  });

  it("surfaces publish failures without clearing the selection", async () => {
    project([{ workspace_id: "w1" }]);
    api.listWorkspaceFiles.mockResolvedValue({
      path: "",
      entries: [{ name: "REQ.dxf", path: "REQ.dxf", type: "file", size: 12 }],
    });
    api.readWorkspaceFile.mockResolvedValue({
      bytes: new TextEncoder().encode("DXF"),
      mimeType: "application/dxf",
    });
    api.publishArtifact.mockRejectedValue(
      new HttpError({ code: "artifact_too_large", status: 413, message: "too large" }),
    );
    const wrapper = mountPanel();
    await flushPromises();
    await wrapper
      .findAll("button")
      .find((button) => button.text().includes("REQ.dxf"))!
      .trigger("click");
    await flushPromises();
    await wrapper
      .findAll("button")
      .find((button) => button.text().includes("Publish artifact"))!
      .trigger("click");
    await flushPromises();
    expect(wrapper.text()).toContain("Artifact is too large.");
    expect(wrapper.text()).toContain("REQ.dxf");
  });
});
