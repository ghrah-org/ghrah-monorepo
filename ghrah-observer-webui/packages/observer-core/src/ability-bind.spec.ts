// SPDX-FileCopyrightText: 2026 chenxya <chenxya@ghrah.org>
//
// SPDX-License-Identifier: Apache-2.0

import type { ServerMessage, WebSocketLike } from "@ghrah/protocol";
import { AbilityAdminViewSchema, AbilityApiStatusSchema, EventType } from "@ghrah/protocol";
import { createPinia, setActivePinia } from "pinia";
import { afterEach, describe, expect, it, vi } from "vitest";

import { setAbilityControlClientFactory } from "./ability-client.js";
import { connectStores } from "./bind.js";
import { ObserverClient } from "./client.js";
import { createSyncBatcher } from "./frame-batcher.js";
import { useAbilityAdminStore } from "./stores/ability-admin.js";
import { useAbilityPackagesStore } from "./stores/ability-packages.js";

const status = AbilityApiStatusSchema.parse({
  ability_source: "builtin",
  features: {
    tree: true,
    grants_write: false,
    versions_read: false,
    versions_write: false,
    packages_read: false,
    packages_install: false,
    surface_write: false,
  },
});

const admin = AbilityAdminViewSchema.parse({
  project_id: "p1",
  agent_id: "a1",
  revision: 8,
  lifecycle: { state: "stopped", requires_respawn: false, pending_reasons: [] },
  grants: [],
  bound_tools: [],
});

function message(type: string, payload: Record<string, unknown>): ServerMessage {
  return { type, payload } as ServerMessage;
}

async function connect(): Promise<{
  client: ObserverClient;
  disconnect: () => void;
}> {
  const sockets: WebSocketLike[] = [];
  const client = new ObserverClient("ws://localhost:4111/ws", "observer", {
    wsFactory: () => {
      const socket: WebSocketLike = {
        readyState: 1,
        onopen: null,
        onmessage: null,
        onclose: null,
        onerror: null,
        send() {},
        close() {},
      };
      sockets.push(socket);
      return socket;
    },
  });
  vi.spyOn(client, "pluginNegotiate").mockResolvedValue({
    request_id: "neg",
    success: true,
    data: {},
  } as never);
  vi.spyOn(client, "taskDump").mockResolvedValue({
    request_id: "dump",
    success: true,
    data: {},
  } as never);
  vi.spyOn(
    client as unknown as { _syncInitialState: () => Promise<void> },
    "_syncInitialState",
  ).mockResolvedValue();
  const disconnect = connectStores(client, { batcher: createSyncBatcher() });
  const pending = client.connect();
  sockets[0]?.onopen?.();
  await pending;
  return { client, disconnect };
}

describe("ability invalidation events", () => {
  afterEach(() => setAbilityControlClientFactory(null));

  it("rereads a visible agent from the server and does not copy the event revision", async () => {
    setActivePinia(createPinia());
    const adminRead = vi.fn(async () => admin);
    setAbilityControlClientFactory(
      () =>
        ({
          tree: async () => Promise.reject(new Error("tree down")),
          admin: adminRead,
          changes: async () => ({ changes: [], next_after_seq: 0, has_more: false }),
          status: async () => status,
        }) as never,
    );
    const store = useAbilityAdminStore();
    await store.setVisible("p1", "a1", true);
    expect(adminRead).toHaveBeenCalledTimes(1);
    const { client, disconnect } = await connect();
    try {
      (
        client as unknown as { _dispatch: (type: string, message: ServerMessage) => void }
      )._dispatch(
        EventType.ABILITY_TREE_CHANGED,
        message(EventType.ABILITY_TREE_CHANGED, {
          project_id: "p1",
          agent_id: "a1",
          revision: 99,
          reason: "grant",
        }),
      );
      await vi.waitFor(() => expect(adminRead).toHaveBeenCalledTimes(2));
      expect(store.record("p1", "a1")?.revision).toBe(8);
    } finally {
      disconnect();
    }
  });

  it("rereads package status when a package event arrives while settings are open", async () => {
    setActivePinia(createPinia());
    const statusRead = vi.fn(async () => status);
    setAbilityControlClientFactory(() => ({ status: statusRead }) as never);
    const packages = useAbilityPackagesStore();
    packages.setSettingsVisible(true);
    await vi.waitFor(() => expect(statusRead).toHaveBeenCalledTimes(1));
    const { client, disconnect } = await connect();
    try {
      (
        client as unknown as { _dispatch: (type: string, message: ServerMessage) => void }
      )._dispatch(
        EventType.ABILITY_PACKAGE_CHANGED,
        message(EventType.ABILITY_PACKAGE_CHANGED, { action: "installed" }),
      );
      await vi.waitFor(() => expect(statusRead).toHaveBeenCalledTimes(2));
    } finally {
      disconnect();
    }
  });
});
