// SPDX-FileCopyrightText: 2026 chenxya <chenxya@ghrah.org>
//
// SPDX-License-Identifier: Apache-2.0

import {
  AbilityAdminViewSchema,
  AbilityChangesPageSchema,
  AbilityTreeViewSchema,
} from "@ghrah/protocol";
import { createPinia, setActivePinia } from "pinia";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

import { setAbilityControlClientFactory } from "../ability-client.js";
import type { AbilityControlClient } from "../ability-control.js";
import { useAbilityAdminStore } from "./ability-admin.js";

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
});
