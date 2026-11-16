// SPDX-FileCopyrightText: 2026 chenxya <chenxya@ghrah.org>
//
// SPDX-License-Identifier: Apache-2.0

import { describe, expect, it } from "vitest";

import { HttpClient, HttpError } from "./http.js";
import {
  createProjectControlClient,
  projectErrorResult,
  toProjectCreatePayload,
} from "./project-control.js";

interface RecordedCall {
  method: string;
  path: string;
  body?: unknown;
  query?: unknown;
}

function fakeHttp(response: unknown = {}) {
  const calls: RecordedCall[] = [];
  const read = (method: string) => async (path: string, options?: unknown) => {
    calls.push({ method, path, query: (options as { query?: unknown } | undefined)?.query });
    return response;
  };
  const write = (method: string) => async (path: string, body: unknown) => {
    calls.push({ method, path, body });
    return response;
  };
  const http = {
    get: read("GET"),
    post: write("POST"),
    patch: write("PATCH"),
    put: write("PUT"),
    delete: read("DELETE"),
  } as unknown as HttpClient;
  return { http, calls };
}

describe("createProjectControlClient", () => {
  it("lists with archived=false by default and forwards status", async () => {
    const { http, calls } = fakeHttp({ projects: [], count: 0 });
    const client = createProjectControlClient(http);
    await client.list();
    await client.list({ archived: true, status: "paused" });
    expect(calls[0]).toMatchObject({
      method: "GET",
      path: "/api/v1/projects",
      query: { archived: false, status: undefined },
    });
    expect(calls[1]).toMatchObject({
      query: { archived: true, status: "paused" },
    });
  });

  it("uses encoded project ids and the documented action subpaths", async () => {
    const { http, calls } = fakeHttp({ project_id: "p", name: "n", version: 1 });
    const client = createProjectControlClient(http);
    await client.get("p/1");
    await client.archive("p", 2);
    await client.restore("p", 3);
    await client.pause("p", 4);
    await client.resume("p", 5);
    await client.stop("p");
    expect(calls.map((call) => call.path)).toEqual([
      "/api/v1/projects/p%2F1",
      "/api/v1/projects/p/archive",
      "/api/v1/projects/p/restore",
      "/api/v1/projects/p/pause",
      "/api/v1/projects/p/resume",
      "/api/v1/projects/p/stop",
    ]);
    expect(calls[1]?.body).toEqual({ expected_version: 2 });
    expect(calls[5]?.body).toEqual({});
  });

  it("sends update bodies without a duplicate project id", async () => {
    const { http, calls } = fakeHttp({ project_id: "p", name: "n", version: 2 });
    const client = createProjectControlClient(http);
    await client.update("p", {
      name: "renamed",
      expected_version: 1,
    });
    expect(calls[0]?.path).toBe("/api/v1/projects/p");
    expect(calls[0]?.body).toEqual({ name: "renamed", expected_version: 1 });
  });

  it("puts delete options in the query string", async () => {
    const { http, calls } = fakeHttp({
      project_id: "p",
      deleted: true,
      storage_purged: true,
      rooms_deleted: 0,
    });
    const client = createProjectControlClient(http);
    await client.delete("p", { expectedVersion: 3 });
    await client.delete("p", { expectedVersion: 4, cascadeRooms: true });
    expect(calls[0]).toMatchObject({
      method: "DELETE",
      query: { expected_version: 3 },
    });
    expect(calls[1]?.query).toEqual({ expected_version: 4, cascade_rooms: true });
  });
});

describe("projectErrorResult", () => {
  it("renames conflict to the UI-visible version conflict code", () => {
    const result = projectErrorResult(
      new HttpError({
        code: "conflict",
        status: 409,
        message: "Project p modified: expected version 2, got 3",
        detail: { current_version: 3 },
      }),
    );
    expect(result.success).toBe(false);
    expect(result.error).toBe("project_version_conflict");
    expect(result.error_detail).toContain("got 3");
  });

  it("passes other wire codes through", () => {
    expect(
      projectErrorResult(
        new HttpError({ code: "project_has_rooms", status: 409, message: "rooms" }),
      ).error,
    ).toBe("project_has_rooms");
    expect(
      projectErrorResult(new HttpError({ code: "not_found", status: 404, message: "missing" }))
        .error,
    ).toBe("not_found");
  });

  it("maps unknown errors to unavailable", () => {
    const result = projectErrorResult(new Error("boom"));
    expect(result.error).toBe("unavailable");
    expect(result.error_detail).toBe("boom");
  });
});

describe("toProjectCreatePayload", () => {
  it("keeps the WS sparse wire shape", () => {
    expect(toProjectCreatePayload("p", {})).toEqual({ name: "p" });
    expect(toProjectCreatePayload("p", { writableWorkspaces: [] })).toEqual({
      name: "p",
      writable_workspaces: [],
    });
    expect(
      toProjectCreatePayload("p", {
        description: "d",
        manifestRef: "ns.ref",
        projectRootLocator: "/srv/p",
        shellCwd: "/srv/p",
        writableWorkspaces: [
          { locator: "/srv/p", name: "default", role: "default", defaultForAgents: true },
        ],
      }),
    ).toEqual({
      name: "p",
      description: "d",
      manifest_ref: "ns.ref",
      project_root_locator: "/srv/p",
      shell_cwd: "/srv/p",
      writable_workspaces: [
        {
          locator: "/srv/p",
          name: "default",
          role: "default",
          default_for_agents: true,
        },
      ],
    });
  });
});
