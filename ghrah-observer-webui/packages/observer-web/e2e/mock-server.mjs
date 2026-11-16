// SPDX-FileCopyrightText: 2026 chenxya <chenxya@ghrah.org>
//
// SPDX-License-Identifier: Apache-2.0

import { spawn } from "node:child_process";

const MOCK_PORT = Number(process.env.MOCK_PORT ?? 4113);
const HEALTH_PORT = Number(process.env.MOCK_HEALTH_PORT ?? 4114);

const child = spawn(
  process.execPath,
  [
    "../mock-server/dist/cli.js",
    "--port",
    String(MOCK_PORT),
    "--http-port",
    String(HEALTH_PORT),
    "--scenario",
    "demo",
    "--replay-on-connect",
    "--no-chains",
    "--hitl-timeout",
    "600000",
  ],
  { stdio: "inherit" },
);

function shutdown() {
  child.kill("SIGTERM");
}

process.on("SIGTERM", shutdown);
process.on("SIGINT", shutdown);
child.on("exit", (code) => {
  process.exit(code ?? 0);
});
