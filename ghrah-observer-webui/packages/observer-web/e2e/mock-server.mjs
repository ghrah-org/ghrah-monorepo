// SPDX-FileCopyrightText: 2026 chenxya <chenxya@ghrah.org>
//
// SPDX-License-Identifier: Apache-2.0

// Playwright 的 webServer 探活要求 HTTP 200–403，而 ws 服务对普通 GET 返回 426，
// 因此这里做一层包装：启动 mock-server，并在 ws 端口可连通后额外暴露 200 的健康端点。
// 参数固定为视觉回归需要的确定性场景（replay-on-connect + 关闭持续链路模拟）。

import { spawn } from "node:child_process";
import { createServer } from "node:http";
import { connect } from "node:net";

const MOCK_PORT = Number(process.env.MOCK_PORT ?? 4113);
const HEALTH_PORT = Number(process.env.MOCK_HEALTH_PORT ?? 4114);

const child = spawn(
  process.execPath,
  [
    "../mock-server/dist/cli.js",
    "--port",
    String(MOCK_PORT),
    "--scenario",
    "demo",
    "--replay-on-connect",
    "--no-chains",
    "--hitl-timeout",
    "600000",
  ],
  { stdio: "inherit" },
);

function waitForPort(port, timeoutMs = 20_000) {
  const deadline = Date.now() + timeoutMs;
  return new Promise((resolve, reject) => {
    const attempt = () => {
      const socket = connect({ host: "127.0.0.1", port }, () => {
        socket.destroy();
        resolve();
      });
      socket.on("error", () => {
        socket.destroy();
        if (Date.now() > deadline) reject(new Error(`mock-server port ${port} not ready`));
        else setTimeout(attempt, 100);
      });
    };
    attempt();
  });
}

await waitForPort(MOCK_PORT);

const health = createServer((req, res) => {
  if (req.url === "/health") {
    res.writeHead(200, { "content-type": "text/plain" });
    res.end("ok");
    return;
  }
  res.writeHead(404);
  res.end();
});
health.listen(HEALTH_PORT, "127.0.0.1");

function shutdown() {
  child.kill("SIGTERM");
  health.close();
  setTimeout(() => process.exit(0), 200).unref();
}

process.on("SIGTERM", shutdown);
process.on("SIGINT", shutdown);
child.on("exit", (code) => {
  health.close();
  process.exit(code ?? 0);
});
