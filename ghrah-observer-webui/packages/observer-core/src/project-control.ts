// SPDX-FileCopyrightText: 2026 chenxya <chenxya@ghrah.org>
//
// SPDX-License-Identifier: Apache-2.0

/** Project 管理的 REST 客户端（观察端边缘）。
 *
 * 只负责 HTTP 形状与错误归一；业务语义全在 Subject 的 ``project_*`` 命令里。
 * baseUrl/token 与能力管理面共用 ``HttpClient`` 约定（回环可省略 token）。
 */

import {
  type CommandResultPayload,
  generateRequestId,
  type ProjectDeletedView,
  type ProjectInfoPayload,
  type ProjectListResultPayload,
  type ProjectUpdateRequest,
} from "@ghrah/protocol";

import { HttpClient, HttpError } from "./http.js";
import { useConnectionStore } from "./stores/connection.js";

/** 建项目入参（camelCase，对齐 ``ObserverClient.createProject``）。 */
export interface ProjectCreateOptionsLike {
  description?: string;
  manifestRef?: string | null;
  projectRootLocator?: string;
  writableWorkspaces?: Array<{
    locator: string;
    name?: string;
    role?: string | null;
    defaultForAgents?: boolean;
  }>;
  shellCwd?: string;
}

/** 列表查询（``archived`` 缺省 false；``status`` 缺省不过滤）。 */
export interface ProjectListQuery {
  archived?: boolean;
  status?: string | null;
}

export interface ProjectDeleteQuery {
  expectedVersion: number;
  cascadeRooms?: boolean;
}

export interface ProjectControlClient {
  list(query?: ProjectListQuery): Promise<ProjectListResultPayload>;
  get(projectId: string): Promise<ProjectInfoPayload>;
  create(body: Record<string, unknown>): Promise<ProjectInfoPayload>;
  update(projectId: string, body: ProjectUpdateRequest): Promise<ProjectInfoPayload>;
  archive(projectId: string, expectedVersion: number): Promise<ProjectInfoPayload>;
  restore(projectId: string, expectedVersion: number): Promise<ProjectInfoPayload>;
  pause(projectId: string, expectedVersion?: number): Promise<ProjectInfoPayload>;
  resume(projectId: string, expectedVersion?: number): Promise<ProjectInfoPayload>;
  stop(projectId: string, expectedVersion?: number): Promise<ProjectInfoPayload>;
  delete(projectId: string, query: ProjectDeleteQuery): Promise<ProjectDeletedView>;
}

function projectPath(projectId: string): string {
  return `/api/v1/projects/${encodeURIComponent(projectId)}`;
}

/** 构建 REST 客户端。 */
export function createProjectControlClient(http: HttpClient): ProjectControlClient {
  const transition = (
    projectId: string,
    action: "pause" | "resume" | "stop",
    expectedVersion?: number,
  ): Promise<ProjectInfoPayload> =>
    http.post<ProjectInfoPayload>(
      `${projectPath(projectId)}/${action}`,
      expectedVersion === undefined ? {} : { expected_version: expectedVersion },
    );

  return {
    list: (query = {}) =>
      http.get<ProjectListResultPayload>("/api/v1/projects", {
        query: {
          archived: query.archived ?? false,
          status: query.status ?? undefined,
        },
      }),
    get: (projectId) => http.get<ProjectInfoPayload>(projectPath(projectId)),
    create: (body) => http.post<ProjectInfoPayload>("/api/v1/projects", body),
    update: (projectId, body) => http.patch<ProjectInfoPayload>(projectPath(projectId), body),
    archive: (projectId, expectedVersion) =>
      http.post<ProjectInfoPayload>(`${projectPath(projectId)}/archive`, {
        expected_version: expectedVersion,
      }),
    restore: (projectId, expectedVersion) =>
      http.post<ProjectInfoPayload>(`${projectPath(projectId)}/restore`, {
        expected_version: expectedVersion,
      }),
    pause: (projectId, expectedVersion) => transition(projectId, "pause", expectedVersion),
    resume: (projectId, expectedVersion) => transition(projectId, "resume", expectedVersion),
    stop: (projectId, expectedVersion) => transition(projectId, "stop", expectedVersion),
    delete: (projectId, query) =>
      http.delete<ProjectDeletedView>(projectPath(projectId), {
        query: {
          expected_version: query.expectedVersion,
          ...(query.cascadeRooms ? { cascade_rooms: true } : {}),
        },
      }),
  };
}

/** 把 camelCase 建项目入参转成 WS/REST 共用的 sparse wire body。
 *
 * 与 ``ObserverClient.createProject`` 原实现逐字段对齐：缺省字段不出现在
 * body 里（服务端填默认值），显式空 workspace 列表保留。
 */
export function toProjectCreatePayload(
  name: string,
  options: ProjectCreateOptionsLike,
): Record<string, unknown> {
  const payload: Record<string, unknown> = { name };
  if (options.description) payload["description"] = options.description;
  if (options.manifestRef != null) payload["manifest_ref"] = options.manifestRef;
  if (options.projectRootLocator) {
    payload["project_root_locator"] = options.projectRootLocator;
  }
  if (options.writableWorkspaces !== undefined) {
    payload["writable_workspaces"] = options.writableWorkspaces.map((workspace) => ({
      locator: workspace.locator,
      ...(workspace.name ? { name: workspace.name } : {}),
      ...(workspace.role !== undefined ? { role: workspace.role } : {}),
      ...(workspace.defaultForAgents !== undefined
        ? { default_for_agents: workspace.defaultForAgents }
        : {}),
    }));
  }
  if (options.shellCwd) payload["shell_cwd"] = options.shellCwd;
  return payload;
}

/** 与组件消费形状对齐的失败回执：``conflict`` 归一为 UI 认识的版本冲突码。 */
export function projectErrorResult(error: unknown): CommandResultPayload {
  if (error instanceof HttpError) {
    return {
      request_id: generateRequestId(),
      success: false,
      data: null,
      error: projectWireCode(error.code),
      error_detail: error.message,
    };
  }
  return {
    request_id: generateRequestId(),
    success: false,
    data: null,
    error: "unavailable",
    error_detail: error instanceof Error ? error.message : String(error),
  };
}

function projectWireCode(code: string): string {
  // 组件/既有 mock 的冲突 UI 认 ``project_version_conflict``；真实 Subject 的
  // WS 回执是描述性文案（历史分歧），REST 适配层统一成 UI 认识的 code。
  return code === "conflict" ? "project_version_conflict" : code;
}

/** 按连接地址构造默认客户端（与 ``abilityControlClient`` 同法）。 */
export function projectControlClient(): ProjectControlClient {
  const connection = useConnectionStore();
  const http = new HttpClient({
    baseUrl: connection.httpBaseUrl,
    token: connection.apiToken,
  });
  return createProjectControlClient(http);
}
