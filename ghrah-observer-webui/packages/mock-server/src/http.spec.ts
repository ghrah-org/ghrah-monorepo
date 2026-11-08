import { afterEach, describe, expect, it } from "vitest";

import { createMockHttpServer, type MockHttpServer } from "./http.js";

const servers: MockHttpServer[] = [];

afterEach(async () => {
  await Promise.all(servers.splice(0).map((server) => server.close()));
});

async function start(conflictOnWrite = false): Promise<string> {
  const server = createMockHttpServer({ port: 0, conflictOnWrite });
  servers.push(server);
  await server.start();
  return `http://127.0.0.1:${server.port}`;
}

async function getJson<T>(url: string): Promise<T> {
  const response = await fetch(url);
  return (await response.json()) as T;
}

describe("MockHttpServer", () => {
  it("serves status, tree and admin projections", async () => {
    const base = await start();
    const status = await getJson<{ ability_source?: string; features?: { tree?: boolean } }>(
      `${base}/api/v1/abilities/status`,
    );
    expect(status.ability_source).toBe("builtin");
    expect(status.features?.tree).toBe(true);
    const tree = await getJson<{ format?: string }>(
      `${base}/api/v1/projects/project-1/agents/agent-1/ability-tree`,
    );
    expect(tree.format).toBe("ghrah.ability-tree.v2");
    const admin = await getJson<{
      format?: string;
      surface?: { effective?: unknown[] };
    }>(`${base}/api/v1/projects/project-1/agents/agent-1/ability-admin`);
    expect(admin.format).toBe("ghrah.ability-control.admin.v1");
    expect(admin.surface?.effective?.length).toBeGreaterThan(0);
    const other = await fetch(
      `${base}/api/v1/projects/demo-project/agents/demo-architect/ability-admin`,
    );
    expect(other.status).toBe(200);
    expect(other.headers.get("access-control-allow-origin")).toBe("*");
    const body = (await other.json()) as { project_id?: string; agent_id?: string };
    expect(body.project_id).toBe("demo-project");
    expect(body.agent_id).toBe("demo-architect");
  });

  it("serves deterministic conflict responses for write endpoints", async () => {
    const base = await start(true);
    const response = await fetch(
      `${base}/api/v1/projects/project-1/agents/agent-1/ability-tree/grants/files`,
      { method: "PUT", body: JSON.stringify({ alias: "files" }) },
    );
    expect(response.status).toBe(409);
    const body = (await response.json()) as {
      error: { code?: string; detail?: { revision?: number } };
    };
    expect(body.error.code).toBe("conflict");
    expect(body.error.detail?.revision).toBeGreaterThan(0);
  });

  it("applies grant, bind, and revoke changes and keeps a stale revision", async () => {
    const base = await start(false);
    const grant = await fetch(
      `${base}/api/v1/projects/demo-project/agents/demo-architect/ability-tree/grants/ghrah-fs`,
      {
        method: "PUT",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({
          alias: "ghrah-fs",
          instance_id: "ghrah-fs",
          members: ["read_file"],
          expected_revision: 1,
          axes: [{ member: "operations/read_file", visible: true, available: false }],
        }),
      },
    );
    expect(grant.status).toBe(200);
    const admin = await getJson<{
      revision?: number;
      lifecycle?: { requires_respawn?: boolean };
      grants?: { axes?: { available?: boolean }[] }[];
    }>(`${base}/api/v1/projects/demo-project/agents/demo-architect/ability-admin`);
    expect(admin.revision).toBe(2);
    expect(admin.lifecycle?.requires_respawn).toBe(true);
    expect(admin.grants?.[0]?.axes?.[0]?.available).toBe(false);

    const stale = await fetch(
      `${base}/api/v1/projects/demo-project/agents/demo-architect/ability-tree/grants/ghrah-fs`,
      {
        method: "PUT",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ alias: "ghrah-fs", members: ["read_file"], expected_revision: 1 }),
      },
    );
    expect(stale.status).toBe(409);
    const unchanged = await getJson<{ revision?: number }>(
      `${base}/api/v1/projects/demo-project/agents/demo-architect/ability-tree`,
    );
    expect(unchanged.revision).toBe(2);

    const removed = await fetch(
      `${base}/api/v1/projects/demo-project/agents/demo-architect/ability-tree/grants/ghrah-fs?expected_revision=2`,
      { method: "DELETE" },
    );
    expect(removed.status).toBe(200);
    const after = await getJson<{ grants?: unknown[] }>(
      `${base}/api/v1/projects/demo-project/agents/demo-architect/ability-admin`,
    );
    expect(after.grants).toEqual([]);
  });

  it("binds and unbinds a granted tool", async () => {
    const base = await start(false);
    const bound = await fetch(
      `${base}/api/v1/projects/project-1/agents/agent-1/ability-tree/tools/read_file`,
      {
        method: "PUT",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({
          alias: "ghrah-fs",
          member: "read_file",
          tool_name: "read_file",
          expected_revision: 1,
        }),
      },
    );
    expect(bound.status).toBe(200);
    const body = (await bound.json()) as {
      view: { revision: number; bound_tools: { name: string }[] };
    };
    expect(body.view.revision).toBe(2);
    expect(body.view.bound_tools.map((tool) => tool.name)).toEqual(["read_file"]);
    const unbound = await fetch(
      `${base}/api/v1/projects/project-1/agents/agent-1/ability-tree/tools/read_file?expected_revision=2`,
      { method: "DELETE" },
    );
    expect(unbound.status).toBe(200);
    const tree = await getJson<{ bound_tools?: unknown[] }>(
      `${base}/api/v1/projects/project-1/agents/agent-1/ability-tree`,
    );
    expect(tree.bound_tools).toEqual([]);
  });
});
