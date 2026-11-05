// SPDX-FileCopyrightText: 2026 chenxya <chenxya@ghrah.org>
//
// SPDX-License-Identifier: Apache-2.0

import { type ChildProcess, spawn } from "node:child_process";

export interface LaunchOptions {
  launchMethod: string;
  binaryPath: string;
}

export class Launcher {
  private coreProcess: ChildProcess | null = null;
  private subjectProcess: ChildProcess | null = null;

  async ensureCore(url: string, opts: LaunchOptions): Promise<void> {
    if (await this.isReachable(url)) {
      return;
    }

    if (opts.launchMethod === "binary" && opts.binaryPath) {
      this.coreProcess = spawn(opts.binaryPath, [], { stdio: "pipe" });
    } else {
      this.coreProcess = spawn("uv", ["run", "ghrah-core"], {
        stdio: "pipe",
        cwd: this.findProjectRoot(),
      });
    }

    this.coreProcess.on("error", (err) => {
      console.error("Core process error:", err);
    });

    await this.waitForReachable(url, 30_000);
  }

  async ensureSubject(observerUrl: string, workspaceRoot: string, coreUrl: string): Promise<void> {
    const args = ["run", "ghrah-subject"];
    if (workspaceRoot) {
      args.push("--workspace-root", workspaceRoot);
    }
    if (coreUrl) {
      args.push("--core-url", coreUrl);
    }

    this.subjectProcess = spawn("uv", args, {
      stdio: "pipe",
      cwd: this.findProjectRoot(),
    });

    this.subjectProcess.on("error", (err) => {
      console.error("Subject process error:", err);
    });

    await this.waitForReachable(observerUrl, 30_000);
  }

  async stopAll(): Promise<void> {
    this.stopProcess(this.subjectProcess);
    this.subjectProcess = null;
    this.stopProcess(this.coreProcess);
    this.coreProcess = null;
  }

  isRunning(): { core: boolean; subject: boolean } {
    return {
      core: this.coreProcess !== null && !this.coreProcess.killed,
      subject: this.subjectProcess !== null && !this.subjectProcess.killed,
    };
  }

  async startCluster(): Promise<void> {
    // TODO: send init_cluster command via WebSocket (payload requires cluster_id:
    // client.initCluster(clusterId, config?))
  }

  async stopCluster(): Promise<void> {
    // TODO: send shutdown_cluster command via WebSocket (payload requires cluster_id:
    // client.shutdownCluster(clusterId))
  }

  private async isReachable(url: string): Promise<boolean> {
    try {
      const wsUrl = new URL(url);
      const response = await fetch(`http://${wsUrl.host}/health`, {
        signal: AbortSignal.timeout(3000),
      });
      return response.ok;
    } catch {
      return false;
    }
  }

  private async waitForReachable(url: string, timeout: number): Promise<void> {
    const start = Date.now();
    while (Date.now() - start < timeout) {
      if (await this.isReachable(url)) return;
      await new Promise((r) => setTimeout(r, 1000));
    }
    throw new Error(`Server not reachable at ${url} after ${timeout}ms`);
  }

  private stopProcess(proc: ChildProcess | null) {
    if (proc && !proc.killed) {
      proc.kill("SIGTERM");
    }
  }

  private findProjectRoot(): string {
    return process.env["GHRAH_PROJECT_ROOT"] ?? process.cwd();
  }
}
