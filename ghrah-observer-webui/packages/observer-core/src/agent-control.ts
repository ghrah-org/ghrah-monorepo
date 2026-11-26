// SPDX-FileCopyrightText: 2026 chenxya <chenxya@ghrah.org>
//
// SPDX-License-Identifier: Apache-2.0

import {
  AbilityTreeIdSchema,
  AgentAdminViewSchema,
  AgentDeletedViewSchema,
  type AgentAdminView,
  type AgentDeletedView,
} from "@ghrah/protocol";
import { HttpClient } from "./http.js";
import { useConnectionStore } from "./stores/connection.js";

/** 期望运行状态；与 `PUT .../desired-state` 的请求体一致。 */
export type AgentDesiredState = "running" | "stopped";

export interface AgentControlClientOptions {
  /** 项目作用域；端点从受信路径取项目，不在请求体里自报。 */
  projectId: string;
}

/**
 * Agent 资源的观察端 REST 客户端。
 *
 * 停止/启动走 `desired-state`（持久语义），删除走 `DELETE` 并带项目版本
 * 做条件写。读面用于拿 `runtime.busy` 与 `project_version`。
 */
export interface AgentControlClient {
  get(agentId: string): Promise<AgentAdminView>;
  setDesiredState(
    agentId: string,
    state: AgentDesiredState,
    expectedRevision: number,
  ): Promise<AgentAdminView>;
  delete(agentId: string, expectedRevision: number): Promise<AgentDeletedView>;
}

export function createAgentControlClient(
  http: HttpClient,
  options: AgentControlClientOptions,
): AgentControlClient {
  const projectId = AbilityTreeIdSchema.parse(options.projectId);
  const base = `/api/v1/projects/${projectId}/agents`;

  function agentPath(agentId: string, suffix = ""): string {
    return `${base}/${AbilityTreeIdSchema.parse(agentId)}${suffix}`;
  }

  function rejectTarget(view: AgentAdminView, agentId: string): void {
    if (view.project_id !== projectId || view.specification.agent_id !== agentId) {
      throw new Error("Agent control target mismatch");
    }
  }

  return {
    async get(agentId) {
      const view = AgentAdminViewSchema.parse(await http.get<unknown>(agentPath(agentId)));
      rejectTarget(view, agentId);
      return view;
    },
    async setDesiredState(agentId, state, expectedRevision) {
      const view = AgentAdminViewSchema.parse(
        await http.put<unknown>(agentPath(agentId, "/desired-state"), {
          expected_revision: expectedRevision,
          state,
        }),
      );
      rejectTarget(view, agentId);
      return view;
    },
    async delete(agentId, expectedRevision) {
      const view = AgentDeletedViewSchema.parse(
        await http.delete<unknown>(agentPath(agentId), {
          query: { expected_revision: expectedRevision },
        }),
      );
      if (view.project_id !== projectId || view.agent_id !== agentId) {
        throw new Error("Agent control target mismatch");
      }
      return view;
    },
  };
}

export type AgentControlClientFactory = (projectId: string) => AgentControlClient;

let factory: AgentControlClientFactory | null = null;

/** 测试注入 Agent 管理面客户端。传 null 恢复为按连接地址创建。 */
export function setAgentControlClientFactory(next: AgentControlClientFactory | null): void {
  factory = next;
}

/** 按项目创建 Agent 管理面客户端；调用方仍须传入通过标识校验的项目 id。 */
export function agentControlClient(projectId: string): AgentControlClient {
  if (factory) return factory(projectId);
  const connection = useConnectionStore();
  const http = new HttpClient({
    baseUrl: connection.httpBaseUrl,
    token: connection.apiToken,
  });
  return createAgentControlClient(http, { projectId });
}
