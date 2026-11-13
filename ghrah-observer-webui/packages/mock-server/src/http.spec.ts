import { afterEach, describe, expect, it } from "vitest";

import { createMockHttpServer, type MockHttpServer } from "./http.js";
import { MockState } from "./state.js";
import { AgentSnapshotSchema, CommandType, EventType } from "@ghrah/protocol";

const servers: MockHttpServer[] = [];

afterEach(async () => {
  await Promise.all(servers.splice(0).map((server) => server.close()));
});

async function start(conflictOnWrite = false, packages = true): Promise<string> {
  const server = createMockHttpServer({ port: 0, conflictOnWrite, packages });
  servers.push(server);
  await server.start();
  return `http://127.0.0.1:${server.port}`;
}

async function getJson<T>(url: string): Promise<T> {
  const response = await fetch(url);
  return (await response.json()) as T;
}

describe("MockHttpServer", () => {
  it("uses WS identities and runtime, emits changes, and restores revoked grants", async () => {
    const state = new MockState();
    const projectResult = state.handleCommand(CommandType.PROJECT_CREATE, { name: "http-shared" });
    const projectId = (projectResult.data as { project: { project_id: string } }).project
      .project_id;
    const created = state.handleCommand(CommandType.AGENT_ENTITY_CREATE, {
      project_id: projectId,
      name: "worker",
      manifest_ref: "mock.agent",
      start: true,
    });
    const snapshot = AgentSnapshotSchema.parse(created.data);
    const events: string[] = [];
    state.emit = (type) => events.push(type);
    const server = createMockHttpServer({ port: 0, state });
    servers.push(server);
    await server.start();
    const base = `http://127.0.0.1:${server.port}`;
    const agentBase = `${base}/api/v1/projects/${projectId}/agents/${snapshot.identity.agent_id}`;
    expect((await fetch(`${base}/health`)).status).toBe(200);
    expect(
      (await fetch(`${base}/api/v1/projects/${projectId}/agents/unknown/ability-admin`)).status,
    ).toBe(404);
    const admin = await getJson<{ lifecycle: { state: string }; run_id: string }>(
      `${agentBase}/ability-admin`,
    );
    expect(admin.lifecycle.state).toBe("running");
    expect(admin.run_id).toBe(snapshot.runtime.incarnation_id);
    expect(
      (
        await fetch(`${agentBase}/ability-tree/grants/ghrah-fs?expected_revision=1`, {
          method: "DELETE",
        })
      ).status,
    ).toBe(200);
    expect(
      (
        await fetch(`${agentBase}/ability-tree/grants/ghrah-fs`, {
          method: "PUT",
          body: JSON.stringify({ alias: "ghrah-fs", members: ["read_file"], expected_revision: 2 }),
        })
      ).status,
    ).toBe(200);
    expect(events.filter((type) => type === EventType.ABILITY_TREE_CHANGED)).toHaveLength(2);
    const restored = await getJson<{ abilities: unknown[] }>(`${agentBase}/ability-tree`);
    expect(restored.abilities).toHaveLength(1);
    const changes = await getJson<{ changes: Array<{ reason: string }> }>(
      `${agentBase}/ability-changes`,
    );
    expect(changes.changes.map((change) => change.reason)).toEqual(["revoke", "grant"]);
    const patchedResponse = await fetch(agentBase, {
      method: "PATCH",
      body: JSON.stringify({
        expected_revision: snapshot.specification_revision,
        system_prompt: "新提示词",
      }),
    });
    expect(patchedResponse.status).toBe(200);
    const patched = (await patchedResponse.json()) as {
      effective: string;
      specification: { project_version: number; prompt: { size_bytes: number } };
    };
    expect(patched.effective).toBe("next_start");
    expect(patched.specification.prompt.size_bytes).toBe(12);
    expect([...state.agents.values()][0].config.system_prompt).toBe("");
    expect(
      (
        await fetch(agentBase, {
          method: "PATCH",
          body: JSON.stringify({
            expected_revision: snapshot.specification_revision,
            system_prompt: "stale",
          }),
        })
      ).status,
    ).toBe(409);
    const stoppedResponse = await fetch(`${agentBase}/desired-state`, {
      method: "PUT",
      body: JSON.stringify({
        expected_revision: patched.specification.project_version,
        state: "stopped",
      }),
    });
    expect(stoppedResponse.status).toBe(200);
    const stopped = (await stoppedResponse.json()) as {
      specification: { project_version: number };
      runtime: { availability: string };
    };
    expect(stopped.runtime.availability).toBe("offline");
    const restartedResponse = await fetch(`${agentBase}/desired-state`, {
      method: "PUT",
      body: JSON.stringify({
        expected_revision: stopped.specification.project_version,
        state: "running",
      }),
    });
    expect(restartedResponse.status).toBe(200);
    const restarted = (await restartedResponse.json()) as {
      specification: { project_version: number };
      ability: { requires_respawn: boolean };
      runtime: { incarnation_id: string };
    };
    expect(restarted.runtime.incarnation_id).not.toBe(snapshot.runtime.incarnation_id);
    expect(restarted.ability.requires_respawn).toBe(false);
    expect([...state.agents.values()][0].config.system_prompt).toBe("新提示词");
    const draftResponse = await fetch(`${base}/api/v1/projects/${projectId}/agents`, {
      method: "POST",
      body: JSON.stringify({
        expected_revision: restarted.specification.project_version,
        name: "draft",
        endpoint_name: "mock-model",
      }),
    });
    expect(draftResponse.status).toBe(201);
    const draft = (await draftResponse.json()) as {
      specification: { agent_id: string; project_version: number };
    };
    const draftBase = `${base}/api/v1/projects/${projectId}/agents/${draft.specification.agent_id}`;
    expect(draftResponse.headers.get("location")).toBe(new URL(draftBase).pathname);
    const list = await getJson<{ agents: Array<{ specification: Record<string, unknown> }> }>(
      `${base}/api/v1/projects/${projectId}/agents`,
    );
    expect(list.agents).toHaveLength(2);
    expect(list.agents[0].specification).not.toHaveProperty("system_prompt");
    const draftStart = await fetch(`${draftBase}/desired-state`, {
      method: "PUT",
      body: JSON.stringify({
        expected_revision: draft.specification.project_version,
        state: "running",
      }),
    });
    expect(draftStart.status).toBe(409);
    expect(((await draftStart.json()) as { error: { code: string } }).error.code).toBe(
      "agent_manifest_required",
    );
    expect(
      (
        await fetch(`${draftBase}?expected_revision=${draft.specification.project_version}`, {
          method: "DELETE",
        })
      ).status,
    ).toBe(200);
    expect((await fetch(draftBase)).status).toBe(404);
    state.dispose();
  });

  it("rejects startup when the HTTP port is occupied", async () => {
    const first = createMockHttpServer({ port: 0, host: "127.0.0.1" });
    servers.push(first);
    await first.start();
    const second = createMockHttpServer({ port: first.port, host: "127.0.0.1" });
    servers.push(second);
    await expect(second.start()).rejects.toMatchObject({ code: "EADDRINUSE" });
  });
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

  it("serves package groups for distribution, project, and agent scopes", async () => {
    const base = await start();
    const distribution = await getJson<{
      scope?: string;
      groups?: {
        full_name?: string | null;
        versions?: {
          origin?: string | null;
          project?: { committed?: boolean; selected?: boolean } | null;
          agent?: { granted?: boolean; alias?: string | null } | null;
        }[];
      }[];
    }>(`${base}/api/v1/ability-package-groups`);
    expect(distribution.scope).toBe("distribution");
    const names = (distribution.groups ?? []).map((g) => g.full_name);
    expect(names).toContain("demo.notes");
    expect(names).toContain("ghrah.fs");
    expect(names).not.toContain(null);
    for (const group of distribution.groups ?? []) {
      for (const version of group.versions ?? []) {
        expect(version.project).toBeNull();
        expect(version.agent).toBeNull();
      }
    }

    const project = await getJson<{
      scope?: string;
      project_id?: string | null;
      groups?: {
        full_name?: string | null;
        versions?: { project?: { committed?: boolean } | null; agent?: unknown }[];
      }[];
    }>(`${base}/api/v1/projects/demo-project/ability-package-groups`);
    expect(project.scope).toBe("project");
    expect(project.project_id).toBe("demo-project");
    const missing = (project.groups ?? []).find((g) => g.full_name === null);
    expect(missing).toBeDefined();
    expect(missing?.versions?.[0]?.project?.committed).toBe(true);
    for (const group of project.groups ?? []) {
      for (const version of group.versions ?? []) {
        expect(version.agent).toBeNull();
      }
    }

    const agentView = await getJson<{
      scope?: string;
      agent_id?: string | null;
      groups?: {
        full_name?: string | null;
        versions?: {
          origin?: string | null;
          project?: { selected?: boolean } | null;
          agent?: { granted?: boolean; alias?: string | null } | null;
        }[];
      }[];
    }>(`${base}/api/v1/projects/demo-project/agents/demo-architect/ability-package-groups`);
    expect(agentView.scope).toBe("agent");
    expect(agentView.agent_id).toBe("demo-architect");
    const fsGroup = (agentView.groups ?? []).find((g) => g.full_name === "ghrah.fs");
    expect(fsGroup?.versions?.[0]?.project?.selected).toBe(true);
    expect(fsGroup?.versions?.[0]?.agent).toEqual({ granted: true, alias: "ghrah-fs" });
    const notesGroup = (agentView.groups ?? []).find((g) => g.full_name === "demo.notes");
    const notesInstalled = notesGroup?.versions?.find((v) => v.origin === "installed");
    expect(notesInstalled?.agent).toEqual({ granted: false, alias: null });
  });
  it("flips the agent grant after revoking the ability", async () => {
    const base = await start();
    const revoked = await fetch(
      `${base}/api/v1/projects/project-1/agents/agent-1/ability-tree/grants/ghrah-fs?expected_revision=1`,
      { method: "DELETE" },
    );
    expect(revoked.status).toBe(200);
    const view = await getJson<{
      groups?: {
        full_name?: string | null;
        versions?: { agent?: { granted?: boolean } | null }[];
      }[];
    }>(`${base}/api/v1/projects/project-1/agents/agent-1/ability-package-groups`);
    const fsGroup = (view.groups ?? []).find((g) => g.full_name === "ghrah.fs");
    expect(fsGroup?.versions?.[0]?.agent?.granted).toBe(false);
  });

  it("closes the package surface when packages are disabled", async () => {
    const base = await start(false, false);
    const status = await getJson<{ features?: { packages_read?: boolean } }>(
      `${base}/api/v1/abilities/status`,
    );
    expect(status.features?.packages_read).toBe(false);
    for (const path of [
      "/api/v1/ability-package-groups",
      "/api/v1/projects/demo-project/ability-package-groups",
      "/api/v1/projects/demo-project/agents/demo-architect/ability-package-groups",
    ]) {
      const response = await fetch(`${base}${path}`);
      expect(response.status).toBe(503);
      const body = (await response.json()) as { error?: { code?: string } };
      expect(body.error?.code).toBe("capability_unavailable");
    }
  });
});
