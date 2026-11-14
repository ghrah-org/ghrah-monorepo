// SPDX-FileCopyrightText: 2026 chenxya <chenxya@ghrah.org>
//
// SPDX-License-Identifier: Apache-2.0

/** ``ObserverClient`` 的 REST 项目适配子类。
 *
 * 只 override 项目方法：`ServerClient.connect()` 内部的 `_syncInitialState()`
 * 会调用 `this.listProjects()`，因此初始同步也自动走 REST；`listAgents` /
 * `listRooms` 等其余方法保持 WS（本轮不切）。
 *
 * 返回形状仍是 `CommandResultPayload`，组件零改动；写操作响应同时投影到
 * store（REST 不会产生 WS `command_result`），WS 事件到达后重复投影幂等。
 */

import {
  type CommandResultPayload,
  generateRequestId,
  type ProjectUpdateRequest,
} from "@ghrah/protocol";

import {
  type CreateProjectOptions,
  type ListProjectsOptions,
  ObserverClient as ObserverClientBase,
  type ProjectDeleteTarget,
  type ProjectLifecycleTarget,
} from "./client.js";
import {
  projectErrorResult,
  type ProjectControlClient,
  toProjectCreatePayload,
} from "./project-control.js";
import {
  applyProjectListResult,
  applyProjectRemoved,
  applyProjectUpsert,
} from "./project-projection.js";

export class RestProjectObserverClient extends ObserverClientBase {
  constructor(
    serverUrl: string,
    private readonly projectApi: ProjectControlClient,
  ) {
    super(serverUrl);
  }

  override async listProjects(options: ListProjectsOptions = {}): Promise<CommandResultPayload> {
    const archived = options.archived ?? false;
    return this.run(async () => {
      const data = await this.projectApi.list({
        archived,
        status: options.status ?? null,
      });
      applyProjectListResult(data, archived);
      return data;
    });
  }

  override async getProject(projectId: string): Promise<CommandResultPayload> {
    return this.run(async () => {
      const project = await this.projectApi.get(projectId);
      applyProjectUpsert(project);
      return { project };
    });
  }

  override async createProject(
    name: string,
    options: CreateProjectOptions,
  ): Promise<CommandResultPayload> {
    return this.run(async () => {
      const project = await this.projectApi.create(toProjectCreatePayload(name, options));
      applyProjectUpsert(project);
      return { project };
    });
  }

  override async updateProject(
    projectId: string,
    fields: {
      name?: string | null;
      description?: string | null;
      manifestRef?: string | null;
      expectedVersion?: number | null;
      shellCwd?: string | null;
    },
  ): Promise<CommandResultPayload> {
    const expectedVersion = fields.expectedVersion;
    if (expectedVersion == null) {
      return {
        request_id: generateRequestId(),
        success: false,
        data: null,
        error: "invalid_request",
        error_detail: "缺少 expected_version",
      };
    }
    return this.run(async () => {
      const body: Record<string, unknown> = { expected_version: expectedVersion };
      if (fields.name != null) body["name"] = fields.name;
      if (fields.description != null) body["description"] = fields.description;
      if (fields.manifestRef != null) body["manifest_ref"] = fields.manifestRef;
      if (fields.shellCwd != null) body["shell_cwd"] = fields.shellCwd;
      const project = await this.projectApi.update(
        projectId,
        body as unknown as ProjectUpdateRequest,
      );
      applyProjectUpsert(project);
      return { project };
    });
  }

  override async archiveProject(target: ProjectLifecycleTarget): Promise<CommandResultPayload> {
    return this.run(async () => {
      const project = await this.projectApi.archive(target.projectId, target.expectedVersion);
      applyProjectUpsert(project);
      return { project };
    });
  }

  override async restoreProject(target: ProjectLifecycleTarget): Promise<CommandResultPayload> {
    return this.run(async () => {
      const project = await this.projectApi.restore(target.projectId, target.expectedVersion);
      applyProjectUpsert(project);
      return { project };
    });
  }

  override async deleteProject(target: ProjectDeleteTarget): Promise<CommandResultPayload> {
    return this.run(async () => {
      const data = await this.projectApi.delete(target.projectId, {
        expectedVersion: target.expectedVersion,
        cascadeRooms: target.cascadeRooms,
      });
      applyProjectRemoved(target.projectId);
      return data;
    });
  }

  // ── API-only：本轮无 UI，先把路径固定下来 ──

  async pauseProject(projectId: string, expectedVersion?: number): Promise<CommandResultPayload> {
    return this.run(async () => {
      const project = await this.projectApi.pause(projectId, expectedVersion);
      applyProjectUpsert(project);
      return { project };
    });
  }

  async resumeProject(projectId: string, expectedVersion?: number): Promise<CommandResultPayload> {
    return this.run(async () => {
      const project = await this.projectApi.resume(projectId, expectedVersion);
      applyProjectUpsert(project);
      return { project };
    });
  }

  async stopProject(projectId: string, expectedVersion?: number): Promise<CommandResultPayload> {
    return this.run(async () => {
      const project = await this.projectApi.stop(projectId, expectedVersion);
      applyProjectUpsert(project);
      return { project };
    });
  }

  private async run<T>(body: () => Promise<T>): Promise<CommandResultPayload> {
    try {
      const data = await body();
      return { request_id: generateRequestId(), success: true, data };
    } catch (error) {
      return projectErrorResult(error);
    }
  }
}
