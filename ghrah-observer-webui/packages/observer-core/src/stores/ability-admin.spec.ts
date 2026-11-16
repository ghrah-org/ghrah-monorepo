// SPDX-FileCopyrightText: 2026 chenxya <chenxya@ghrah.org>
//
// SPDX-License-Identifier: Apache-2.0

import {
  AbilityAdminViewSchema,
  AbilityChangesPageSchema,
  AbilityTreeChangeResultSchema,
  AbilityTreeViewSchema,
} from "@ghrah/protocol";
import { createPinia, setActivePinia } from "pinia";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

import { setAbilityControlClientFactory } from "../ability-client.js";
import { AbilityConflictError, type AbilityControlClient } from "../ability-control.js";
import { HttpError } from "../http.js";
import { replacementGrant, useAbilityAdminStore } from "./ability-admin.js";

const tree = AbilityTreeViewSchema.parse({
  project_id: "p1",
  agent_id: "a1",
  run_id: "run-1",
  revision: 3,
  abilities: [
    {
      alias: "files",
      instance_id: "files",
      instance_revision: 1,
      definition_digest: "sha256:def",
      version_digest: "sha256:abc",
      revision: 3,
      allowed_roots: ["/workspace"],
      operations: [
        {
          key: "read_file",
          description: "Read",
          input_schema: {},
          output_schema: {},
          availability: "ready",
        },
      ],
      availability: "ready",
    },
  ],
  bound_tools: [],
});

const admin = AbilityAdminViewSchema.parse({
  project_id: "p1",
  agent_id: "a1",
  revision: 4,
  lifecycle: { state: "running", requires_respawn: true, pending_reasons: ["respawn"] },
  grants: [
    {
      alias: "files",
      instance_id: "files",
      instance_revision: 1,
      version_digest: "sha256:abc",
      definition_digest: "sha256:def",
      members: ["read_file"],
      axes: [{ member: "operations/read_file", visible: true, available: true }],
      revision: 4,
      source: "admin",
    },
  ],
  bound_tools: [],
  surface: { current_runtime: null, pending_respawn: { added: ["write_file"], removed: [] } },
});

const changes = AbilityChangesPageSchema.parse({
  changes: [
    {
      seq: 1,
      agent: "a1",
      revision: 4,
      reason: "grant",
      actor: {},
      before_revision: 3,
      source: "admin",
    },
    {
      seq: 2,
      agent: "other",
      revision: 1,
      reason: "grant",
      actor: {},
      before_revision: 0,
      source: "admin",
    },
  ],
  next_after_seq: 2,
  has_more: false,
});

function client(overrides: Partial<AbilityControlClient> = {}): AbilityControlClient {
  return {
    status: vi.fn(),
    tree: vi.fn(async () => tree),
    admin: vi.fn(async () => admin),
    changes: vi.fn(async () => changes),
    content: vi.fn(),
    putGrant: vi.fn(),
    revokeGrant: vi.fn(),
    bindTool: vi.fn(),
    unbindTool: vi.fn(),
    packageGroups: vi.fn(),
    projectPackageGroups: vi.fn(),
    agentPackageGroups: vi.fn(),
    packageVersion: vi.fn(),
    probePackageMcp: vi.fn(),
    ...overrides,
  } as AbilityControlClient;
}

describe("useAbilityAdminStore", () => {
  beforeEach(() => setActivePinia(createPinia()));
  afterEach(() => setAbilityControlClientFactory(null));

  it("caches admin, tree, and only this agent's audit", async () => {
    setAbilityControlClientFactory(() => client());
    const store = useAbilityAdminStore();
    await store.load("p1", "a1");
    const record = store.record("p1", "a1");
    expect(record?.revision).toBe(4);
    expect(record?.tree?.run_id).toBe("run-1");
    expect(record?.changes.map((change) => change.seq)).toEqual([1]);
    expect(record?.stale).toBe(false);
    expect(record?.failed).toBe(false);
  });

  it("keeps admin when the runtime tree is unavailable", async () => {
    setAbilityControlClientFactory(() =>
      client({ tree: vi.fn(async () => Promise.reject(new Error("no run"))) }),
    );
    const store = useAbilityAdminStore();
    await store.load("p1", "a1");
    const record = store.record("p1", "a1");
    expect(record?.failed).toBe(false);
    expect(record?.admin?.revision).toBe(4);
    expect(record?.tree).toBeNull();
  });

  it("does not overlap reloads, and a visible agent rereads after it is marked stale", async () => {
    const pending: Array<(value: typeof tree) => void> = [];
    let active = 0;
    let maxActive = 0;
    const treeRead = vi.fn(
      () =>
        new Promise<typeof tree>((resolve) => {
          active += 1;
          maxActive = Math.max(maxActive, active);
          pending.push((value) => {
            active -= 1;
            resolve(value);
          });
        }),
    );
    setAbilityControlClientFactory(() => client({ tree: treeRead }));
    const store = useAbilityAdminStore();
    const first = store.load("p1", "a1");
    const second = store.load("p1", "a1");
    await vi.waitFor(() => expect(treeRead).toHaveBeenCalledTimes(1));
    expect(maxActive).toBe(1);
    pending[0]?.(tree);
    await first;
    await vi.waitFor(() => expect(treeRead).toHaveBeenCalledTimes(2));
    pending[1]?.(tree);
    await second;
    expect(maxActive).toBe(1);

    treeRead.mockResolvedValue(tree);
    await store.setVisible("p1", "a1", true);
    expect(treeRead).toHaveBeenCalledTimes(2);
    await store.markStale("p1", "a1");
    expect(treeRead).toHaveBeenCalledTimes(3);
    await store.setVisible("p1", "a1", false);
    await store.markStale("p1", "a1");
    expect(treeRead).toHaveBeenCalledTimes(3);
    expect(store.record("p1", "a1")?.stale).toBe(true);
  });

  it("rereads admin after a grant replacement and does not repeat a conflict", async () => {
    const updated = AbilityTreeViewSchema.parse({ ...tree, revision: 5 });
    const putGrant = vi.fn(async () =>
      AbilityTreeChangeResultSchema.parse({ view: updated, revision: 5 }),
    );
    const adminRead = vi
      .fn()
      .mockResolvedValueOnce(admin)
      .mockResolvedValueOnce(AbilityAdminViewSchema.parse({ ...admin, revision: 5 }));
    setAbilityControlClientFactory(() =>
      client({ putGrant, admin: adminRead, tree: vi.fn(async () => updated) }),
    );
    const store = useAbilityAdminStore();
    await store.load("p1", "a1");
    const request = replacementGrant(admin.grants[0]!, admin.revision, {
      members: ["operations/read_file"],
      axes: [{ member: "operations/read_file", visible: true, available: false }],
    });
    expect(request.members).toEqual(["read_file"]);
    expect(request.policy).toBeNull();
    expect(request.whole_container).toBe(false);
    await store.replaceGrant("p1", "a1", request);
    expect(putGrant).toHaveBeenCalledTimes(1);
    expect(putGrant).toHaveBeenCalledWith("a1", request);
    expect(adminRead).toHaveBeenCalledTimes(2);
    expect(store.record("p1", "a1")?.revision).toBe(5);
    expect(store.record("p1", "a1")?.conflict).toBe(false);

    putGrant.mockRejectedValueOnce(
      new AbilityConflictError(
        new HttpError({
          code: "conflict",
          status: 409,
          message: "relation revision changed",
          detail: { revision: 9, view: AbilityTreeViewSchema.parse({ ...tree, revision: 9 }) },
        }),
      ),
    );
    await store.replaceGrant("p1", "a1", { ...request, expected_revision: 5 });
    expect(putGrant).toHaveBeenCalledTimes(2);
    expect(adminRead).toHaveBeenCalledTimes(2);
    expect(store.record("p1", "a1")?.conflict).toBe(true);
    expect(store.record("p1", "a1")?.revision).toBe(9);
    expect(store.record("p1", "a1")?.tree?.revision).toBe(9);
    expect(store.record("p1", "a1")?.failed).toBe(false);
  });

  it("prefetches admin only, never the tree or the audit", async () => {
    const adminRead = vi.fn(async () => admin);
    const treeRead = vi.fn(async () => tree);
    const changesRead = vi.fn(async () => changes);
    setAbilityControlClientFactory(() =>
      client({ admin: adminRead, tree: treeRead, changes: changesRead }),
    );
    const store = useAbilityAdminStore();
    await store.prefetch("p1", ["a1"]);
    expect(adminRead).toHaveBeenCalledTimes(1);
    expect(adminRead).toHaveBeenCalledWith("a1");
    expect(treeRead).not.toHaveBeenCalled();
    expect(changesRead).not.toHaveBeenCalled();
    const record = store.record("p1", "a1");
    expect(record?.admin?.revision).toBe(4);
    expect(record?.revision).toBe(4);
    expect(record?.tree).toBeNull();
    expect(record?.stale).toBe(false);
    expect(record?.loading).toBe(false);
    expect(record?.failed).toBe(false);
    expect(record?.partial).toBe(true);
    expect(record?.loadedAt).not.toBeNull();
  });

  it("fills the tree and audit when a prefetched agent becomes visible", async () => {
    const adminRead = vi.fn(async () => admin);
    const treeRead = vi.fn(async () => tree);
    setAbilityControlClientFactory(() => client({ admin: adminRead, tree: treeRead }));
    const store = useAbilityAdminStore();
    await store.prefetch("p1", ["a1"]);
    expect(adminRead).toHaveBeenCalledTimes(1);
    expect(treeRead).not.toHaveBeenCalled();

    await store.setVisible("p1", "a1", true);
    expect(treeRead).toHaveBeenCalledTimes(1);
    const record = store.record("p1", "a1");
    expect(record?.tree?.run_id).toBe("run-1");
    expect(record?.partial).toBe(false);
    expect(record?.loadedAt).not.toBeNull();
  });

  it("prefetches a deduplicated batch of at most twenty agents in order", async () => {
    const adminRead = vi.fn(async (_agentId: string) => admin);
    setAbilityControlClientFactory(() => client({ admin: adminRead }));
    const store = useAbilityAdminStore();
    const ids = Array.from({ length: 25 }, (_, index) => `a${index + 1}`);
    await store.prefetch("p1", ids);
    expect(adminRead.mock.calls.map(([agentId]) => agentId)).toEqual(ids.slice(0, 20));

    adminRead.mockClear();
    await store.prefetch("p1", ["b1", "b1", "b2", "b2", "b1"]);
    expect(adminRead.mock.calls.map(([agentId]) => agentId)).toEqual(["b1", "b2"]);
  });

  it("skips agents that are already loaded or in flight", async () => {
    const adminRead = vi.fn(async (_agentId: string) => admin);
    setAbilityControlClientFactory(() => client({ admin: adminRead }));
    const store = useAbilityAdminStore();
    await store.load("p1", "a1");
    expect(adminRead).toHaveBeenCalledTimes(1);
    adminRead.mockClear();

    const prefetched = store.prefetch("p1", ["a1", "a2"]);
    await store.prefetch("p1", ["a1", "a2"]);
    await prefetched;
    expect(adminRead).toHaveBeenCalledTimes(1);
    expect(adminRead).toHaveBeenCalledWith("a2");
  });

  it("keeps prefetch concurrency at three", async () => {
    let active = 0;
    let maxActive = 0;
    const resolvers: Array<() => void> = [];
    const adminRead = vi.fn(
      () =>
        new Promise<typeof admin>((resolve) => {
          active += 1;
          maxActive = Math.max(maxActive, active);
          resolvers.push(() => {
            active -= 1;
            resolve(admin);
          });
        }),
    );
    setAbilityControlClientFactory(() => client({ admin: adminRead }));
    const store = useAbilityAdminStore();
    const ids = Array.from({ length: 6 }, (_, index) => `a${index + 1}`);
    const done = store.prefetch("p1", ids);

    await vi.waitFor(() => expect(resolvers).toHaveLength(3));
    expect(adminRead).toHaveBeenCalledTimes(3);
    expect(maxActive).toBe(3);

    resolvers.splice(0).forEach((resolve) => resolve());
    await vi.waitFor(() => expect(adminRead).toHaveBeenCalledTimes(6));
    expect(maxActive).toBe(3);

    resolvers.splice(0).forEach((resolve) => resolve());
    await done;
    expect(store.record("p1", "a6")?.loadedAt).not.toBeNull();
  });

  it("records a prefetch failure without pretending the agent is loaded", async () => {
    setAbilityControlClientFactory(() =>
      client({ admin: vi.fn(async () => Promise.reject(new Error("admin down"))) }),
    );
    const store = useAbilityAdminStore();
    await store.prefetch("p1", ["a1"]);
    const record = store.record("p1", "a1");
    expect(record?.failed).toBe(true);
    expect(record?.errorMessage).toBe("admin down");
    expect(record?.loadedAt).toBeNull();
    expect(record?.loading).toBe(false);
    expect(record?.admin).toBeNull();
  });

  it("does not let a prefetch in flight replace the full load that follows it", async () => {
    const pendingAdmin: Array<(value: typeof admin) => void> = [];
    const adminRead = vi.fn(
      () =>
        new Promise<typeof admin>((resolve) => {
          pendingAdmin.push(resolve);
        }),
    );
    setAbilityControlClientFactory(() => client({ admin: adminRead }));
    const store = useAbilityAdminStore();
    const prefetching = store.prefetch("p1", ["a1"]);
    await vi.waitFor(() => expect(adminRead).toHaveBeenCalledTimes(1));

    const loading = store.load("p1", "a1");
    pendingAdmin.splice(0).forEach((resolve) => resolve(admin));
    await prefetching;
    await vi.waitFor(() => expect(adminRead).toHaveBeenCalledTimes(2));
    pendingAdmin.splice(0).forEach((resolve) => resolve(admin));
    await loading;

    const record = store.record("p1", "a1");
    expect(record?.tree?.run_id).toBe("run-1");
    expect(record?.changes.map((change) => change.seq)).toEqual([1]);
    expect(record?.loadedAt).not.toBeNull();
  });

  it("clears one agent's record, visible entry, and focus", async () => {
    setAbilityControlClientFactory(() => client());
    const store = useAbilityAdminStore();
    await store.prefetch("p1", ["a1"]);
    await store.setVisible("p1", "a1", true);
    store.focusAgent("p1", "a1");
    expect(store.record("p1", "a1")).not.toBeNull();
    expect(store.visible.has("p1:a1")).toBe(true);
    expect(store.focus).toEqual({ projectId: "p1", agentId: "a1" });

    store.clearAgent("p1", "a1");
    expect(store.record("p1", "a1")).toBeNull();
    expect(store.visible.has("p1:a1")).toBe(false);
    expect(store.focus).toBeNull();
  });

  it("invalidates an in-flight prefetch when the agent is cleared", async () => {
    const pendingAdmin: Array<(value: typeof admin) => void> = [];
    const adminRead = vi.fn(
      () =>
        new Promise<typeof admin>((resolve) => {
          pendingAdmin.push(resolve);
        }),
    );
    setAbilityControlClientFactory(() => client({ admin: adminRead }));
    const store = useAbilityAdminStore();
    const prefetching = store.prefetch("p1", ["a1"]);
    await vi.waitFor(() => expect(adminRead).toHaveBeenCalledTimes(1));

    store.clearAgent("p1", "a1");
    pendingAdmin.splice(0).forEach((resolve) => resolve(admin));
    await prefetching;
    expect(store.record("p1", "a1")).toBeNull();
  });

  it("keeps skill content when replacing an operation grant", () => {
    const request = replacementGrant(
      {
        ...admin.grants[0]!,
        content: ["skill/files", "skill/files/assets/guide"],
        roots: ["/workspace"],
      },
      4,
      { members: ["read_file"], axes: [] },
    );
    expect(request.skills).toEqual([
      { key: "files", body: true, assets: ["guide"], references: [], scripts: [] },
    ]);
    expect(request.allowed_roots).toEqual(["/workspace"]);
  });
});
