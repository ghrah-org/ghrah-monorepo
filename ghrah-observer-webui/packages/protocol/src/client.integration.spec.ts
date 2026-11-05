// SPDX-FileCopyrightText: 2026 chenxya <chenxya@ghrah.org>
//
// SPDX-License-Identifier: Apache-2.0

import { afterEach, describe, expect, it } from "vitest";
import WebSocket from "ws";
import { createCommand, createPing } from "./builders.js";
import { ServerClient } from "./client.js";
import { CommandType, SystemType } from "./enums.js";
import type { ServerMessage } from "./message.js";

const SERVER_URL = process.env.SERVER_URL ?? "ws://localhost:8080/ws";
const RUN_INTEGRATION = process.env.RUN_INTEGRATION === "1";

describe.skipIf(!RUN_INTEGRATION)("ServerClient Integration", () => {
  let client: ServerClient;

  afterEach(async () => {
    if (client?.connected) {
      await client.disconnect();
    }
  });

  it("connects and receives welcome command_result", async () => {
    client = new ServerClient(SERVER_URL, "observer", {
      wsFactory: (url: string) =>
        new WebSocket(url) as unknown as import("./client.js").WebSocketLike,
    });

    const welcomePromise = new Promise<ServerMessage>((resolve) => {
      client.on(SystemType.COMMAND_RESULT, (msg) => {
        if (msg.payload && (msg.payload as Record<string, unknown>).session_id) {
          resolve(msg);
        }
      });
    });

    await client.connect();
    const welcome = await welcomePromise;

    expect(welcome.type).toBe(SystemType.COMMAND_RESULT);
    expect((welcome.payload as Record<string, unknown>).success).toBe(true);
  }, 10_000);

  it("ping/pong round-trip", async () => {
    client = new ServerClient(SERVER_URL, "observer", {
      wsFactory: (url: string) =>
        new WebSocket(url) as unknown as import("./client.js").WebSocketLike,
    });

    await client.connect();

    const pongPromise = new Promise<ServerMessage>((resolve) => {
      client.on(SystemType.PONG, resolve);
    });

    await client.send(createPing());
    const pong = await pongPromise;

    expect(pong.type).toBe(SystemType.PONG);
  }, 10_000);

  it("subscribe and receive command_result response", async () => {
    client = new ServerClient(SERVER_URL, "observer", {
      wsFactory: (url: string) =>
        new WebSocket(url) as unknown as import("./client.js").WebSocketLike,
    });

    await client.connect();

    const subscribeMsg = createCommand(CommandType.SUBSCRIBE, {
      agent_names: ["*"],
    });

    const result = await client.request(subscribeMsg, 5000);

    expect(result.success).toBe(true);
    expect(result.request_id).toBeDefined();
  }, 10_000);

  it("list_agents returns command_result (may fail without subject)", async () => {
    client = new ServerClient(SERVER_URL, "observer", {
      wsFactory: (url: string) =>
        new WebSocket(url) as unknown as import("./client.js").WebSocketLike,
    });

    await client.connect();

    const listMsg = createCommand(CommandType.LIST_AGENTS, {});

    const result = await client.request(listMsg, 5000);

    expect(result.request_id).toBeDefined();
  }, 10_000);
});
