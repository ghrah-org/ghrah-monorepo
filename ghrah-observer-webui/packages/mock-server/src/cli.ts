#!/usr/bin/env node

// SPDX-FileCopyrightText: 2026 chenxya <chenxya@ghrah.org>
//
// SPDX-License-Identifier: Apache-2.0

import { getScenario, listScenarios, runScenario } from "./scenarios/index.js";
import { MockServer } from "./server.js";
import { MockState } from "./state.js";

interface CliOptions {
  port: number;
  scenario: string;
  hitlTimeoutMs: number;
  chains: boolean;
  replayOnConnect: boolean;
}

function parseArgs(argv: string[]): CliOptions {
  const opts: CliOptions = {
    port: 4113,
    scenario: "demo",
    hitlTimeoutMs: 30_000,
    chains: true,
    replayOnConnect: false,
  };
  for (let i = 0; i < argv.length; i++) {
    const arg = argv[i];
    const next = () => argv[++i];
    switch (arg) {
      case "--port":
        opts.port = Number(next());
        break;
      case "--scenario":
        opts.scenario = next();
        break;
      case "--hitl-timeout":
        opts.hitlTimeoutMs = Number(next());
        break;
      case "--no-chains":
        opts.chains = false;
        break;
      case "--replay-on-connect":
        opts.replayOnConnect = true;
        break;
      case "--help":
      case "-h":
        console.log(
          [
            "Usage: ghrah-mock-server [--port N] [--scenario NAME] [--hitl-timeout MS] [--no-chains] [--replay-on-connect]",
            "",
            "--replay-on-connect: 新连接也回放已缓冲事件（演示/视觉测试用；默认只回放带 last_seq_id 的增量）",
            "",
            `Available scenarios: ${listScenarios()
              .map((s) => s.name)
              .join(", ")}`,
            "",
            "Defaults: --port 4113 --scenario demo（默认 4113 避开真实 server 4112）",
          ].join("\n"),
        );
        process.exit(0);
        break;
      default:
        console.error(`unknown argument: ${arg}`);
        process.exit(2);
    }
  }
  if (!Number.isInteger(opts.port) || opts.port <= 0) {
    console.error(`invalid --port: ${opts.port}`);
    process.exit(2);
  }
  return opts;
}

async function main(): Promise<void> {
  const opts = parseArgs(process.argv.slice(2));
  const scenario = getScenario(opts.scenario);

  const state = new MockState({
    hitlTimeoutMs: opts.hitlTimeoutMs,
    simulateChains: opts.chains,
  });
  const server = new MockServer({
    port: opts.port,
    state,
    replayOnConnect: opts.replayOnConnect,
    logger: (l) => console.log(`[mock] ${l}`),
  });
  await server.start();

  const cancel = runScenario(scenario, {
    state,
    apply: (type, payload) => {
      state.handleCommand(type, payload);
    },
  });
  console.log(`[mock] scenario "${scenario.name}" started (port ${server.port})`);

  const shutdown = async () => {
    cancel();
    await server.close();
    process.exit(0);
  };
  process.on("SIGINT", shutdown);
  process.on("SIGTERM", shutdown);
}

main().catch((err) => {
  console.error(err);
  process.exit(1);
});
