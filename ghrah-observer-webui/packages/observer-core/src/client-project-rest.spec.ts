// SPDX-FileCopyrightText: 2026 chenxya <chenxya@ghrah.org>
//
// SPDX-License-Identifier: Apache-2.0

import {
  ProjectDeletedViewSchema,
  ProjectInfoPayloadSchema,
  type ProjectInfoPayload,
  type ProjectListResultPayload,
} from "@ghrah/protocol";
import { createPinia, setActivePinia } from "pinia";
import { beforeEach, describe, expect, it, vi } from "vitest";

import { RestProjectObserverClient } from "./client-project-rest.js";
import { HttpError } from "./http.js";
import type { ProjectControlClient } from "./project-control.js";
import { useProjectsStore } from "./stores/projects.js";

function project(projectId: string, overrides: Record<string, unknown> = {}): ProjectInfoPayload {
  return ProjectInfoPayloadSchema.parse({
    project_id: projectId,
    name: `project-${projectId}`,
    ...overrides,
  });
}

function listResult(...projects: ProjectInfoPayload[]): ProjectListResultPayload {
  return { projects, count: projects.length };
}

function fakeApi(overrides: Partial<ProjectControlClient> = {}): ProjectControlClient {
  return {
    list: vi.fn(async () => listResult()),
    get: vi.fn(async (projectId: string) => project(projectId)),
    create: vi.fn(async () => project("created")),
    update: vi.fn(async (projectId: string) => project(projectId)),
    archive: vi.fn(async (projectId: string) => project(projectId, { archived_at: "2026-01-01" })),
    restore: vi.fn(async (projectId: string) => project(projectId)),
    pause: vi.fn(async (projectId: string) => project(projectId, { status: "paused" })),
    resume: vi.fn(async (projectId: string) => project(projectId)),
    stop: vi.fn(async (projectId: string) => project(projectId, { status: "stopped" })),
    delete: vi.fn(async (projectId: string) =>
      ProjectDeletedViewSchema.parse({
        project_id: projectId,
        deleted: true,
        storage_purged: true,
        rooms_deleted: 0,
      }),
    ),
    ...overrides,
  };
}

function clientWith(api: ProjectControlClient): RestProjectObserverClient {
  return new RestProjectObserverClient("ws://127.0.0.1:1/ws", api);
}

beforeEach(() => {
  setActivePinia(createPinia());
});

describe("RestProjectObserverClient", () => {
  it("keeps CommandResultPayload shape and projects the REST list into the store", async () => {
    const store = useProjectsStore();
    store.setProjectsFromList([project("stale")]);
    const api = fakeApi({ list: vi.fn(async () => listResult(project("p1"), project("p2"))) });
    const result = await clientWith(api).listProjects();
    expect(result.success).toBe(true);
    expect((result.data as ProjectListResultPayload).count).toBe(2);
    expect([...store.projects.keys()]).toEqual(["p1", "p2"]);
    expect(store.activeProjectId).toBeNull();
  });

  it("honours the archived axis and drops removed archived projects", async () => {
    const store = useProjectsStore();
    store.setProjectsFromList([project("archived-old", { archived_at: "2026-01-01" })]);
    const api = fakeApi({
      list: vi.fn(async () => listResult(project("archived-new", { archived_at: "2026-02-02" }))),
    });
    const result = await clientWith(api).listProjects({ archived: true });
    expect(result.success).toBe(true);
    expect(api.list).toHaveBeenCalledWith({ archived: true, status: null });
    expect([...store.projects.keys()]).toEqual(["archived-new"]);
  });

  it("upserts detail, create and update responses", async () => {
    const store = useProjectsStore();
    const api = fakeApi({ update: vi.fn(async (id: string) => project(id, { name: "renamed" })) });
    const client = clientWith(api);

    const detail = await client.getProject("p1");
    expect(detail.success).toBe(true);
    expect(store.projects.get("p1")?.name).toBe("project-p1");

    const created = await client.createProject("new-project", { description: "d" });
    expect(created.success).toBe(true);
    expect(api.create).toHaveBeenCalledWith({ name: "new-project", description: "d" });
    expect(store.projects.has("created")).toBe(true);

    const updated = await client.updateProject("p1", {
      name: "renamed",
      expectedVersion: 2,
    });
    expect(updated.success).toBe(true);
    expect(api.update).toHaveBeenCalledWith("p1", { expected_version: 2, name: "renamed" });
    expect(store.projects.get("p1")?.name).toBe("renamed");
  });

  it("rejects updates without expected_version before touching the API", async () => {
    const api = fakeApi();
    const result = await clientWith(api).updateProject("p1", { name: "x" });
    expect(result.success).toBe(false);
    expect(result.error).toBe("invalid_request");
    expect(api.update).not.toHaveBeenCalled();
  });

  it("maps REST conflicts to project_version_conflict for the existing UI", async () => {
    const api = fakeApi({
      update: vi.fn(async () => {
        throw new HttpError({
          code: "conflict",
          status: 409,
          message: "Project p1 modified: expected version 1, got 2",
          detail: { current_version: 2 },
        });
      }),
    });
    const result = await clientWith(api).updateProject("p1", {
      name: "x",
      expectedVersion: 1,
    });
    expect(result.success).toBe(false);
    expect(result.error).toBe("project_version_conflict");
  });

  it("upserts lifecycle responses and removes deleted projects", async () => {
    const store = useProjectsStore();
    const api = fakeApi({ update: vi.fn(async (id: string) => project(id, { name: "renamed" })) });
    const client = clientWith(api);

    expect((await client.archiveProject({ projectId: "p1", expectedVersion: 1 })).success).toBe(
      true,
    );
    expect(api.archive).toHaveBeenCalledWith("p1", 1);
    expect(store.projects.get("p1")?.archived_at).not.toBeNull();

    expect((await client.restoreProject({ projectId: "p1", expectedVersion: 2 })).success).toBe(
      true,
    );
    expect(api.restore).toHaveBeenCalledWith("p1", 2);

    const deleted = await client.deleteProject({ projectId: "p1", expectedVersion: 3 });
    expect(deleted.success).toBe(true);
    expect(api.delete).toHaveBeenCalledWith("p1", { expectedVersion: 3, cascadeRooms: undefined });
    expect(store.projects.has("p1")).toBe(false);
  });

  it("exposes API-only transitions without UI consumers", async () => {
    const api = fakeApi();
    const client = clientWith(api);
    expect((await client.pauseProject("p1", 1)).success).toBe(true);
    expect((await client.resumeProject("p1", 2)).success).toBe(true);
    expect((await client.stopProject("p1")).success).toBe(true);
    expect(api.pause).toHaveBeenCalledWith("p1", 1);
    expect(api.resume).toHaveBeenCalledWith("p1", 2);
    expect(api.stop).toHaveBeenCalledWith("p1", undefined);
  });

  it("routes the initial sync project list through REST", async () => {
    const api = fakeApi({ list: vi.fn(async () => listResult(project("p1"))) });
    const client = clientWith(api);
    // connect 未建立 WS 时，_syncInitialState 的 WS 调用会立即失败并被吞掉；
    // 但项目列表这一支必须走 REST（override 生效）。
    await (client as unknown as { _syncInitialState(): Promise<void> })._syncInitialState();
    expect(api.list).toHaveBeenCalledWith({ archived: false, status: null });
    expect(useProjectsStore().projects.has("p1")).toBe(true);
  });
});
