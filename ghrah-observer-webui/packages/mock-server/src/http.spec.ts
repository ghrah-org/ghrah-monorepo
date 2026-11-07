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

  it("increments the revision on successful writes", async () => {
    const base = await start(false);
    const response = await fetch(
      `${base}/api/v1/projects/project-1/agents/agent-1/ability-tree/tools/read_file`,
      { method: "PUT", body: JSON.stringify({ tool_name: "read_file" }) },
    );
    expect(response.status).toBe(200);
    const body = (await response.json()) as { view: { revision: number } };
    expect(body.view.revision).toBeGreaterThan(1);
  });
});
