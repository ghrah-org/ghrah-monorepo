// SPDX-FileCopyrightText: 2026 chenxya <chenxya@ghrah.org>
//
// SPDX-License-Identifier: Apache-2.0

import {
  HitlRequestListViewSchema,
  RoomTranscriptSchema,
  TurnDetailSchema,
  TurnListSchema,
  type TurnRollbackPreviewView,
  TurnRollbackPreviewViewSchema,
} from "@ghrah/protocol";
import {
  type HttpAbortSignal,
  type HttpClient,
  HttpError,
  type HttpRequestOptions,
} from "./http.js";

interface ReadSchema<T> {
  safeParse(
    value: unknown,
  ): { success: true; data: T } | { success: false; error: { issues: readonly unknown[] } };
}

interface ReadOptions {
  signal?: HttpAbortSignal;
}

export interface TranscriptPageOptions extends ReadOptions {
  beforeSeq?: number;
  sinceSeq?: number;
  limit?: number;
}

interface SessionReadOptions extends ReadOptions {
  sessionId?: string;
}

export interface TurnListOptions extends SessionReadOptions {
  status?: string;
  limit?: number;
}

export interface RollbackPreviewOptions extends ReadOptions {
  target?: "base" | "node";
  nodeId?: string | null;
}

function projectPath(projectId: string) {
  return `/api/v1/projects/${encodeURIComponent(projectId)}`;
}

function agentPath(projectId: string, agentId: string) {
  return `${projectPath(projectId)}/agents/${encodeURIComponent(agentId)}`;
}

export class TurnApiClient {
  constructor(private readonly http: HttpClient) {}

  async roomTranscript(projectId: string, roomId: string, options: TranscriptPageOptions = {}) {
    if (options.beforeSeq !== undefined && options.sinceSeq !== undefined) {
      throw new HttpError({
        code: "invalid_request",
        status: 0,
        message: "before_seq 与 since_seq 只能提供一个",
      });
    }
    return this.get(
      RoomTranscriptSchema,
      `${projectPath(projectId)}/rooms/${encodeURIComponent(roomId)}/transcript`,
      {
        query: { before_seq: options.beforeSeq, since_seq: options.sinceSeq, limit: options.limit },
        signal: options.signal,
      },
    );
  }

  listTurns(projectId: string, agentId: string, options: TurnListOptions = {}) {
    return this.get(TurnListSchema, `${agentPath(projectId, agentId)}/turns`, {
      query: { session_id: options.sessionId, status: options.status, limit: options.limit },
      signal: options.signal,
    });
  }

  turnDetail(projectId: string, agentId: string, turnId: string, options: ReadOptions = {}) {
    return this.get(
      TurnDetailSchema,
      `${agentPath(projectId, agentId)}/turns/${encodeURIComponent(turnId)}`,
      options,
    );
  }

  hitlPending(projectId: string, agentId: string, options: SessionReadOptions = {}) {
    return this.get(HitlRequestListViewSchema, `${agentPath(projectId, agentId)}/hitl/pending`, {
      query: { session_id: options.sessionId },
      signal: options.signal,
    });
  }

  /**
   * 读取回滚预览（只读）。
   *
   * `target` 默认 `base`；`target=node` 缺 `nodeId` 时本地拒绝，不发出非法配对。
   */
  async rollbackPreview(
    projectId: string,
    agentId: string,
    turnId: string,
    options: RollbackPreviewOptions = {},
  ): Promise<TurnRollbackPreviewView> {
    const target = options.target ?? "base";
    const nodeId = options.nodeId ?? null;
    if (target === "node" && !nodeId) {
      throw new HttpError({
        code: "invalid_request",
        status: 0,
        message: "target=node 时 node_id 必填",
      });
    }
    return this.get(
      TurnRollbackPreviewViewSchema,
      `${agentPath(projectId, agentId)}/turns/${encodeURIComponent(turnId)}/rollback-preview`,
      {
        query: { target, ...(target === "node" ? { node_id: nodeId } : {}) },
        signal: options.signal,
      },
    );
  }

  private async get<T>(
    schema: ReadSchema<T>,
    path: string,
    options: HttpRequestOptions,
  ): Promise<T> {
    const result = schema.safeParse(await this.http.get<unknown>(path, options));
    if (!result.success) {
      throw new HttpError({
        code: "internal",
        status: 200,
        message: `读面响应与协议不一致：${path}`,
        detail: result.error.issues,
      });
    }
    return result.data;
  }
}
