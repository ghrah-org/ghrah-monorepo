import { afterEach, describe, expect, it, vi } from "vitest";

import { createMockHttpServer, type MockHttpServer } from "./http.js";
import { MockState } from "./state.js";
import {
  AgentSnapshotSchema,
  CommandType,
  EventType,
  HitlRequestListViewSchema,
  RoomInfoPayloadSchema,
  RoomTranscriptSchema,
  TurnDetailSchema,
  TurnListSchema,
  TurnRollbackPreviewViewSchema,
} from "@ghrah/protocol";

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
  it("serves Room transcript, turns and pending HITL from shared state", async () => {
    const state = new MockState({ simulateDeliveries: true, deliveryIntervalMs: 100 });
    const server = createMockHttpServer({ port: 0, host: "127.0.0.1", state });
    servers.push(server);
    await server.start();
    vi.useFakeTimers({ toFake: ["setTimeout", "clearTimeout"] });
    try {
      const project = state.handleCommand(CommandType.PROJECT_CREATE, { name: "transcript-http" });
      const projectId = (project.data as { project: { project_id: string } }).project.project_id;
      function createAgent(name: string) {
        const agent = state.handleCommand(CommandType.AGENT_ENTITY_CREATE, {
          project_id: projectId,
          name,
          template_id: "mock.agent",
          revision: 1,
          start: true,
        });
        return AgentSnapshotSchema.parse(agent.data).identity.agent_id;
      }
      const worker = createAgent("worker");
      const peer = createAgent("peer");
      const created = state.handleCommand(CommandType.ROOM_CREATE, {
        project_id: projectId,
        name: "read counts",
      });
      const room = RoomInfoPayloadSchema.parse((created.data as { room: unknown }).room);
      for (const agentId of [worker, peer]) {
        expect(
          state.handleCommand(CommandType.ROOM_JOIN, {
            room_id: room.room_id,
            subject: agentId,
            subject_type: "agent",
          }).success,
        ).toBe(true);
      }
      const base = `http://127.0.0.1:${server.port}/api/v1/projects/${projectId}`;
      const transcript = `${base}/rooms/${room.room_id}/transcript`;
      expect(RoomTranscriptSchema.parse(await getJson(transcript))).toMatchObject({
        latest_seq: 0,
        next_since_seq: null,
        entries: [],
      });
      expect((await fetch(`${transcript}?before_seq=1&since_seq=0`)).status).toBe(400);
      expect((await fetch(`${transcript}?limit=201`)).status).toBe(400);
      expect(
        state.handleCommand(CommandType.ROOM_SEND, {
          room_id: room.room_id,
          author: "user",
          author_type: "human",
          data: { message: "start", targets: [worker, peer] },
        }).success,
      ).toBe(true);

      await vi.advanceTimersByTimeAsync(100);
      const active = RoomTranscriptSchema.parse(await getJson(`${transcript}?since_seq=0`));
      expect(active).toMatchObject({ next_since_seq: 1, latest_seq: 1, next_before_seq: null });
      expect(active.turns).toHaveLength(2);
      expect(
        active.turns.every((turn) => turn.opener_message_id === active.entries[0].message_id),
      ).toBe(true);
      const turnsUrl = `${base}/agents/${worker}/turns`;
      const listing = TurnListSchema.parse(await getJson(`${turnsUrl}?status=open&limit=1`));
      expect(listing.items).toHaveLength(1);

      await vi.advanceTimersByTimeAsync(100);
      expect(RoomTranscriptSchema.parse(await getJson(`${transcript}?since_seq=1`))).toMatchObject({
        entries: [],
        turns: [],
        next_since_seq: 1,
      });
      const reread = RoomTranscriptSchema.parse(
        await getJson(`${transcript}?before_seq=2&limit=1`),
      );
      expect(reread.entries[0].deliveries.map(({ state }) => state)).toEqual([
        "completed",
        "completed",
      ]);
      expect(
        TurnDetailSchema.parse(await getJson(`${turnsUrl}/${listing.items[0].turn_id}`)).turn
          .status,
      ).toBe("closed");
      expect((await fetch(`${base}/agents/${peer}/turns/${listing.items[0].turn_id}`)).status).toBe(
        404,
      );

      const remember = {
        exact_key: "shell-key",
        exact_label: "ls",
        class_key: null,
        class_label: null,
      };
      const request = state.triggerHitl("worker", "shell", {}, {}, projectId, remember);
      const pending = `${base}/agents/${worker}/hitl/pending`;
      expect(HitlRequestListViewSchema.parse(await getJson(pending)).items).toMatchObject([
        { promise_id: request.promise_id, shell_remember: remember },
      ]);
      state.resolveHitl(request.promise_id, true, null);
      expect(HitlRequestListViewSchema.parse(await getJson(pending)).items).toEqual([]);
    } finally {
      state.dispose();
      vi.useRealTimers();
    }
  });
  it("returns scoped not-found and archive errors for the Room transcript", async () => {
    const state = new MockState();
    const server = createMockHttpServer({ port: 0, host: "127.0.0.1", state });
    servers.push(server);
    await server.start();
    try {
      const owner = state.handleCommand(CommandType.PROJECT_CREATE, { name: "owner" });
      const foreign = state.handleCommand(CommandType.PROJECT_CREATE, { name: "foreign" });
      const { project: ownedProject } = owner.data as { project: { project_id: string } };
      const { project: foreignProject } = foreign.data as { project: { project_id: string } };
      const projectId = ownedProject.project_id;
      const created = state.handleCommand(CommandType.ROOM_CREATE, {
        project_id: projectId,
        name: "hidden history",
      });
      const room = RoomInfoPayloadSchema.parse((created.data as { room: unknown }).room);
      const root = `http://127.0.0.1:${server.port}/api/v1/projects`;
      const transcript = (id: string) => `${root}/${id}/rooms/${room.room_id}/transcript`;
      async function expectError(url: string, status: number, code: string) {
        const response = await fetch(url);
        expect(response.status).toBe(status);
        expect(await response.json()).toMatchObject({ error: { code } });
      }

      await expectError(transcript(foreignProject.project_id), 404, "not_found");
      expect(
        state.handleCommand(CommandType.ROOM_ARCHIVE, {
          room_id: room.room_id,
          expected_version: room.version,
        }).success,
      ).toBe(true);
      await expectError(transcript(projectId), 409, "resource_archived");
      expect(
        state.handleCommand(CommandType.PROJECT_ARCHIVE, {
          project_id: projectId,
          expected_version: state.projects.get(projectId)?.version,
        }).success,
      ).toBe(true);
      await expectError(transcript(projectId), 409, "project_archived");
    } finally {
      state.dispose();
    }
  });

  it("uses WS identities and runtime, emits changes, and restores revoked grants", async () => {
    const state = new MockState();
    const projectResult = state.handleCommand(CommandType.PROJECT_CREATE, { name: "http-shared" });
    const projectId = (projectResult.data as { project: { project_id: string } }).project
      .project_id;
    const created = state.handleCommand(CommandType.AGENT_ENTITY_CREATE, {
      project_id: projectId,
      name: "worker",
      template_id: "mock.agent",
      revision: 1,
      start: true,
    });
    expect(created).toMatchObject({ success: true });
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
        presentation: { title: "改过的标题" },
      }),
    });
    expect(patchedResponse.status).toBe(200);
    const patched = (await patchedResponse.json()) as {
      specification: { project_version: number; presentation: { title: string } };
    };
    // 记录只承载展示字段；配置由 Core 实例快照承载，不再经 PATCH 改写。
    expect(patched.specification.presentation.title).toBe("改过的标题");
    expect(
      (
        await fetch(agentBase, {
          method: "PATCH",
          body: JSON.stringify({
            expected_revision: snapshot.specification_revision,
            presentation: { title: "stale" },
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
    // 配置不再随创建请求进入记录：重启后用的是模板填充的那一份。
    expect([...state.agents.values()][0].config.system_prompt).toContain("mock Agent");
    const draftResponse = await fetch(`${base}/api/v1/projects/${projectId}/agents`, {
      method: "POST",
      body: JSON.stringify({
        expected_revision: restarted.specification.project_version,
        name: "draft",
        template_id: "mock.agent",
        revision: 1,
        desired_state: "stopped",
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
    // 记录不带配置也能启动：配置由模板在创建时填进 Core 快照。
    expect(draftStart.status).toBe(200);
    // 启动已经推进了 project.version，删除要用当前值而不是创建时的快照值。
    const currentVersion = (state.projects.get(projectId) as { version: number }).version;
    expect(
      (
        await fetch(`${draftBase}?expected_revision=${currentVersion}`, {
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

describe("MockHttpServer project REST", () => {
  async function startWithState(): Promise<{ base: string; state: MockState }> {
    const state = new MockState();
    const server = createMockHttpServer({ port: 0, state });
    servers.push(server);
    await server.start();
    return { base: `http://127.0.0.1:${server.port}`, state };
  }

  async function createProject(base: string): Promise<Record<string, unknown>> {
    const response = await fetch(`${base}/api/v1/projects`, {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ name: "rest-project" }),
    });
    expect(response.status).toBe(201);
    expect(response.headers.get("location")).toMatch(/^\/api\/v1\/projects\//);
    return (await response.json()) as Record<string, unknown>;
  }

  it("creates, lists, reads and patches projects", async () => {
    const { base } = await startWithState();
    const project = await createProject(base);
    const projectId = project.project_id as string;
    expect(project.version).toBe(1);

    const list = await getJson<{ projects: unknown[]; count: number }>(`${base}/api/v1/projects`);
    expect(list.count).toBe(1);

    const patched = await fetch(`${base}/api/v1/projects/${projectId}`, {
      method: "PATCH",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ name: "renamed", expected_version: 1 }),
    });
    expect(patched.status).toBe(200);
    expect(((await patched.json()) as { version: number }).version).toBe(2);

    const stale = await fetch(`${base}/api/v1/projects/${projectId}`, {
      method: "PATCH",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ name: "stale", expected_version: 1 }),
    });
    expect(stale.status).toBe(409);
    const staleBody = (await stale.json()) as {
      error: { code: string; detail: { current_version: number } };
    };
    expect(staleBody.error.code).toBe("conflict");
    expect(staleBody.error.detail.current_version).toBe(2);

    const detail = await getJson<{ name: string }>(`${base}/api/v1/projects/${projectId}`);
    expect(detail.name).toBe("renamed");
  });

  it("archives and restores with the archived list axis", async () => {
    const { base } = await startWithState();
    const project = await createProject(base);
    const projectId = project.project_id as string;

    const archived = await fetch(`${base}/api/v1/projects/${projectId}/archive`, {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ expected_version: 1 }),
    });
    expect(archived.status).toBe(200);
    const archivedBody = (await archived.json()) as { archived_at: string | null; version: number };
    expect(archivedBody.archived_at).not.toBeNull();

    const active = await getJson<{ count: number }>(`${base}/api/v1/projects`);
    expect(active.count).toBe(0);
    const archivedList = await getJson<{ count: number }>(`${base}/api/v1/projects?archived=true`);
    expect(archivedList.count).toBe(1);

    const restored = await fetch(`${base}/api/v1/projects/${projectId}/restore`, {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ expected_version: archivedBody.version }),
    });
    expect(restored.status).toBe(200);
    expect(((await restored.json()) as { archived_at: string | null }).archived_at).toBeNull();
  });

  it("guards deletion with room count and supports cascade", async () => {
    const { base, state } = await startWithState();
    const project = await createProject(base);
    const projectId = project.project_id as string;
    const room = state.handleCommand(CommandType.ROOM_CREATE, {
      project_id: projectId,
      name: "room-1",
    });
    expect(room.success).toBe(true);

    const blocked = await fetch(`${base}/api/v1/projects/${projectId}?expected_version=1`, {
      method: "DELETE",
    });
    expect(blocked.status).toBe(409);
    const blockedBody = (await blocked.json()) as {
      error: { code: string; detail: { room_count: number } };
    };
    expect(blockedBody.error.code).toBe("project_has_rooms");
    expect(blockedBody.error.detail.room_count).toBe(1);

    const deleted = await fetch(
      `${base}/api/v1/projects/${projectId}?expected_version=1&cascade_rooms=true`,
      { method: "DELETE" },
    );
    expect(deleted.status).toBe(200);
    const deletedBody = (await deleted.json()) as { rooms_deleted: number; deleted: boolean };
    expect(deletedBody.deleted).toBe(true);
    expect(deletedBody.rooms_deleted).toBe(1);

    const gone = await fetch(`${base}/api/v1/projects/${projectId}`);
    expect(gone.status).toBe(404);
  });

  it("validates pause version and requires expected_version on delete", async () => {
    const { base } = await startWithState();
    const project = await createProject(base);
    const projectId = project.project_id as string;

    const stale = await fetch(`${base}/api/v1/projects/${projectId}/pause`, {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ expected_version: 99 }),
    });
    expect(stale.status).toBe(409);
    const staleBody = (await stale.json()) as {
      error: { code: string; detail: { current_version: number } };
    };
    expect(staleBody.error.detail.current_version).toBe(1);

    const paused = await fetch(`${base}/api/v1/projects/${projectId}/pause`, {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({}),
    });
    expect(paused.status).toBe(200);
    expect(((await paused.json()) as { status: string }).status).toBe("paused");

    const missingVersion = await fetch(`${base}/api/v1/projects/${projectId}`, {
      method: "DELETE",
    });
    expect(missingVersion.status).toBe(400);
  });

  it("returns 503 for project routes without shared state", async () => {
    const base = await start();
    const response = await fetch(`${base}/api/v1/projects`);
    expect(response.status).toBe(503);
  });
});

describe("MockHttpServer turn control reads", () => {
  it("serves rollback preview and validates the target/node_id pairing", async () => {
    const state = new MockState();
    const server = createMockHttpServer({ port: 0, host: "127.0.0.1", state });
    servers.push(server);
    await server.start();
    try {
      const projectId = (
        state.handleCommand(CommandType.PROJECT_CREATE, { name: "preview-http" }).data as {
          project: { project_id: string };
        }
      ).project.project_id;
      const agentId = AgentSnapshotSchema.parse(
        state.handleCommand(CommandType.AGENT_ENTITY_CREATE, {
          project_id: projectId,
          name: "worker",
          template_id: "mock.agent",
          revision: 1,
          start: true,
        }).data,
      ).identity.agent_id;
      state.handleCommand(CommandType.ROOM_CREATE, { project_id: projectId, name: "Preview" });
      const room = [...state.rooms.values()][0];
      const ids = state.seedTurnControlTurns(room.room_id, agentId);
      if (!ids) throw new Error("failed to seed turn-control turns");
      const base = `http://127.0.0.1:${server.port}/api/v1/projects/${projectId}/agents/${agentId}/turns`;
      const previewUrl = (turnId: string) => `${base}/${turnId}/rollback-preview`;

      const defaultPreview = TurnRollbackPreviewViewSchema.parse(
        await getJson(previewUrl(ids.closed)),
      );
      expect(defaultPreview).toMatchObject({ turn_id: ids.closed, target: "base" });
      const nodePreview = TurnRollbackPreviewViewSchema.parse(
        await getJson(`${previewUrl(ids.closed)}?target=node&node_id=node-x`),
      );
      expect(nodePreview).toMatchObject({ target: "node", target_node_id: "node-x" });

      expect((await fetch(`${previewUrl(ids.closed)}?target=node`)).status).toBe(400);
      expect((await fetch(`${previewUrl(ids.closed)}?node_id=node-x`)).status).toBe(400);
      expect((await fetch(previewUrl("missing"))).status).toBe(404);
      expect((await fetch(previewUrl(ids.open))).status).toBe(409);
    } finally {
      state.dispose();
    }
  });
});
