import { describe, expect, it, vi } from "vitest";
import fixture from "../../protocol/src/__fixtures__/ability-tree.json";
import { AbilityConflictError, createAbilityControlClient } from "./ability-control.js";
import { HttpClient, HttpError, type HttpFetch, type HttpFetchResponse } from "./http.js";

function jsonResponse(body: unknown, status = 200): HttpFetchResponse {
  return {
    ok: status >= 200 && status < 300,
    status,
    headers: {
      get: (name: string) =>
        name.toLowerCase() === "content-type" ? "application/json; charset=utf-8" : null,
    },
    json: async () => JSON.parse(JSON.stringify(body)),
    text: async () => JSON.stringify(body),
  };
}

function clientWith(fetchImpl: HttpFetch) {
  return createAbilityControlClient(
    new HttpClient({ baseUrl: "http://localhost:4112", fetchImpl }),
    { projectId: fixture.initial.project_id },
  );
}

describe("AbilityControlClient", () => {
  it("reads the tree and rejects responses for another target", async () => {
    const fetchImpl = vi.fn<HttpFetch>().mockResolvedValue(jsonResponse(fixture.initial));
    const client = clientWith(fetchImpl);
    await expect(client.tree(fixture.initial.agent_id)).resolves.toEqual(fixture.initial);
    expect(fetchImpl.mock.calls[0]?.[0]).toContain(
      `/api/v1/projects/${fixture.initial.project_id}/agents/${fixture.initial.agent_id}/ability-tree`,
    );
    await expect(client.tree("bob")).rejects.toThrow("target mismatch");
  });

  it("maps 409 to an authoritative conflict error without retrying", async () => {
    const view = { ...fixture.initial, revision: fixture.initial.revision + 1 };
    const fetchImpl = vi.fn<HttpFetch>().mockResolvedValue(
      jsonResponse(
        {
          error: {
            code: "conflict",
            message: "relation revision changed",
            detail: { revision: view.revision, view },
          },
        },
        409,
      ),
    );
    const client = clientWith(fetchImpl);
    const error = await client
      .putGrant(fixture.initial.agent_id, {
        alias: "files",
        instance_id: "files-1",
        members: [],
        allowed_roots: [],
        skills: [],
        skill: null,
        whole_container: true,
        axes: [],
        policy: null,
        expected_revision: fixture.initial.revision,
      })
      .catch((caught: unknown) => caught);
    expect(error).toBeInstanceOf(AbilityConflictError);
    expect((error as AbilityConflictError).revision).toBe(view.revision);
    expect((error as AbilityConflictError).view?.revision).toBe(view.revision);
    expect(fetchImpl).toHaveBeenCalledTimes(1);
  });

  it("sends revoke members through the query and rejects invalid content members", async () => {
    const change = {
      format: "ghrah.ability-control.change.v1",
      view: { ...fixture.initial, revision: fixture.initial.revision + 1 },
      revision: fixture.initial.revision + 1,
    };
    const fetchImpl = vi.fn<HttpFetch>().mockResolvedValue(jsonResponse(change));
    const client = clientWith(fetchImpl);
    await client.revokeGrant(fixture.initial.agent_id, {
      alias: "files",
      expected_revision: fixture.initial.revision,
      members: ["operations/read_file"],
    });
    const [url, init] = fetchImpl.mock.calls[0] as [string, { method: string }];
    expect(init.method).toBe("DELETE");
    expect(url).toContain("expected_revision=");
    expect(url).toContain("members=operations%2Fread_file");
    await expect(
      client.content(fixture.initial.agent_id, "files", "skill/guide/../secret"),
    ).rejects.toThrow("Invalid content member");
  });

  it("reads one package-group shape for distribution, project, and agent", async () => {
    const projectId = fixture.initial.project_id;
    const agentId = fixture.initial.agent_id;
    const fetchImpl = vi.fn<HttpFetch>(async (url) => {
      const path = String(url);
      if (path.includes(`/agents/${agentId}/ability-package-groups`)) {
        return jsonResponse({
          scope: "agent",
          installed_available: false,
          project_id: projectId,
          agent_id: agentId,
          groups: [],
        });
      }
      if (path.includes(`/projects/${projectId}/ability-package-groups`)) {
        return jsonResponse({
          scope: "project",
          installed_available: false,
          project_id: projectId,
          groups: [],
        });
      }
      return jsonResponse({ scope: "distribution", installed_available: false, groups: [] });
    });
    const client = clientWith(fetchImpl);
    await expect(client.packageGroups()).resolves.toMatchObject({ scope: "distribution" });
    await expect(client.projectPackageGroups()).resolves.toMatchObject({
      scope: "project",
      project_id: projectId,
    });
    await expect(client.agentPackageGroups(agentId)).resolves.toMatchObject({
      scope: "agent",
      agent_id: agentId,
    });
    const urls = fetchImpl.mock.calls.map((call) => String(call[0]));
    expect(urls[0]).toContain("/api/v1/ability-package-groups");
    expect(urls[0]).not.toContain("/projects/");
    expect(urls[1]).toContain(`/api/v1/projects/${projectId}/ability-package-groups`);
    expect(urls[2]).toContain(`/agents/${agentId}/ability-package-groups`);
    fetchImpl.mockResolvedValueOnce(
      jsonResponse({
        scope: "agent",
        installed_available: false,
        project_id: projectId,
        agent_id: "someone-else",
        groups: [],
      }),
    );
    await expect(client.agentPackageGroups(agentId)).rejects.toThrow("target mismatch");
  });

  it("reads a package version and rejects a mismatched or loose payload", async () => {
    const body = {
      namespace: "ghrah",
      name: "fs",
      full_name: "ghrah.fs",
      version: "0.1.0",
      origin: "builtin",
      lock_state: "builtin",
    };
    const fetchImpl = vi.fn<HttpFetch>().mockResolvedValue(jsonResponse(body));
    const client = clientWith(fetchImpl);
    await expect(client.packageVersion("ghrah", "fs", "0.1.0")).resolves.toMatchObject({
      full_name: "ghrah.fs",
      format: "ghrah.ability-control.package-version.v1",
    });
    const [url, init] = fetchImpl.mock.calls[0] as [string, { method: string }];
    expect(init.method).toBe("GET");
    expect(url).toContain("/api/v1/ability-packages/ghrah/fs/versions/0.1.0");

    fetchImpl.mockResolvedValueOnce(
      jsonResponse({ ...body, name: "other", full_name: "ghrah.other" }),
    );
    await expect(client.packageVersion("ghrah", "fs", "0.1.0")).rejects.toThrow("target mismatch");

    fetchImpl.mockResolvedValueOnce(jsonResponse({ ...body, root: "/tmp/ability" }));
    await expect(client.packageVersion("ghrah", "fs", "0.1.0")).rejects.toThrow();
  });

  it("rejects illegal package identity before sending a version request", async () => {
    const fetchImpl = vi.fn<HttpFetch>();
    const client = clientWith(fetchImpl);
    await expect(client.packageVersion("Ghrah", "fs", "0.1.0")).rejects.toThrow(
      "Invalid package identity",
    );
    await expect(client.packageVersion("ghrah", "fs", "1.2")).rejects.toThrow(
      "Invalid package version",
    );
    await expect(client.packageVersion("ghrah", "../fs", "0.1.0")).rejects.toThrow(
      "Invalid package identity",
    );
    expect(fetchImpl).not.toHaveBeenCalled();
  });

  it("posts a probe with the frozen defaults and a strict response", async () => {
    const body = {
      namespace: "mcp",
      name: "local",
      version: "0.1.0",
      server: "notes",
      ok: true,
      phase: "list_tools",
      elapsed_ms: 4,
      tool_count: 0,
      resource_count: 0,
      error: null,
    };
    const fetchImpl = vi.fn<HttpFetch>().mockResolvedValue(jsonResponse(body));
    const client = clientWith(fetchImpl);
    await expect(client.probePackageMcp("mcp", "local", "0.1.0")).resolves.toMatchObject({
      server: "notes",
      ok: true,
      format: "ghrah.ability-control.mcp-probe.v1",
    });
    const [url, init] = fetchImpl.mock.calls[0] as [string, { method: string; body?: string }];
    expect(init.method).toBe("POST");
    expect(url).toContain("/api/v1/ability-packages/mcp/local/versions/0.1.0/mcp/probe");
    expect(JSON.parse(init.body ?? "")).toEqual({ timeout_ms: 5000, include_resources: false });

    await client.probePackageMcp("mcp", "local", "0.1.0", {
      timeoutMs: 2000,
      includeResources: true,
    });
    expect(JSON.parse(fetchImpl.mock.calls[1]?.[1].body ?? "")).toEqual({
      timeout_ms: 2000,
      include_resources: true,
    });

    fetchImpl.mockResolvedValueOnce(jsonResponse({ ...body, version: "0.2.0" }));
    await expect(client.probePackageMcp("mcp", "local", "0.1.0")).rejects.toThrow(
      "target mismatch",
    );
    fetchImpl.mockResolvedValueOnce(jsonResponse({ ...body, url: "http://secret" }));
    await expect(client.probePackageMcp("mcp", "local", "0.1.0")).rejects.toThrow();
  });

  it("rejects an out-of-range probe timeout before sending", async () => {
    const fetchImpl = vi.fn<HttpFetch>();
    const client = clientWith(fetchImpl);
    await expect(
      client.probePackageMcp("mcp", "local", "0.1.0", { timeoutMs: 999 }),
    ).rejects.toThrow();
    await expect(
      client.probePackageMcp("mcp", "local", "0.1.0", { timeoutMs: 10001 }),
    ).rejects.toThrow();
    expect(fetchImpl).not.toHaveBeenCalled();
  });

  it("aborts a probe at timeoutMs plus 3000", async () => {
    vi.useFakeTimers();
    try {
      const fetchImpl = vi.fn<HttpFetch>(
        (_url, init) =>
          new Promise<HttpFetchResponse>((_resolve, reject) => {
            init.signal.addEventListener("abort", () => {
              reject(Object.assign(new Error("aborted"), { name: "AbortError" }));
            });
          }),
      );
      const client = clientWith(fetchImpl);
      let error: unknown;
      const pending = client.probePackageMcp("mcp", "local", "0.1.0", { timeoutMs: 5000 }).then(
        () => {
          error = new Error("probe resolved");
        },
        (caught: unknown) => {
          error = caught;
        },
      );
      await vi.advanceTimersByTimeAsync(7999);
      expect(fetchImpl).toHaveBeenCalledTimes(1);
      expect(error).toBeUndefined();
      await vi.advanceTimersByTimeAsync(1);
      await pending;
      expect(error).toBeInstanceOf(HttpError);
      expect((error as HttpError).status).toBe(0);
    } finally {
      vi.useRealTimers();
    }
  });
});
