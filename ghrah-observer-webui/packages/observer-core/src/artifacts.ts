// SPDX-FileCopyrightText: 2026 chenxya <chenxya@ghrah.org>
//
// SPDX-License-Identifier: Apache-2.0

/**
 * 项目产物与工作区浏览的只读/发布客户端。
 *
 * K6 不走协议生成链：DTO 在观察端本地定义（与 omni M3 任务书 §2.2 对齐），
 * 服务端返回按宽松校验收窄；错误沿用 {@link HttpError}（服务端 error.code 优先）。
 */

import { HttpError, type HttpAbortSignal } from "./http.js";
import { useConnectionStore } from "./stores/connection.js";

const DEFAULT_JSON_TIMEOUT_MS = 30_000;
/** 预览/下载可能达 64 MiB，给更长预算。 */
const DEFAULT_BYTES_TIMEOUT_MS = 120_000;

const STATUS_CODES: Readonly<Record<number, string>> = {
  400: "invalid_request",
  403: "access_denied",
  404: "artifact_not_found",
  413: "artifact_too_large",
  415: "unsupported_artifact_type",
  503: "capability_unavailable",
};

export interface ArtifactSourceView {
  workspace_id: string;
  path: string;
  agent_id?: string | null;
}

export interface ArtifactView {
  artifact_id: string;
  filename: string;
  mime_type: string;
  size: number;
  title?: string | null;
  published_at: string;
  source: ArtifactSourceView;
}

export interface WorkspaceEntryView {
  name: string;
  path: string;
  type: "file" | "dir";
  size?: number | null;
  mtime?: string | null;
}

export interface WorkspaceListingView {
  path: string;
  entries: WorkspaceEntryView[];
}

export interface PublishArtifactRequest {
  workspace_id: string;
  path: string;
  mime_type?: string | null;
  title?: string | null;
}

export interface ArtifactBytes {
  bytes: Uint8Array;
  mimeType: string;
}

/** 浏览器/Node 共用的最小 fetch 形状（observer-core 不依赖 DOM lib）。 */
export interface ArtifactsFetchInit {
  method: string;
  headers: Record<string, string>;
  body?: string;
  signal?: HttpAbortSignal;
}

export interface ArtifactsFetchResponse {
  readonly ok: boolean;
  readonly status: number;
  readonly headers: { get(name: string): string | null };
  json(): Promise<unknown>;
  text(): Promise<string>;
  arrayBuffer(): Promise<ArrayBuffer>;
}

export type ArtifactsFetch = (
  url: string,
  init: ArtifactsFetchInit,
) => Promise<ArtifactsFetchResponse>;

export interface ArtifactsClientOptions {
  baseUrl?: string;
  token?: string;
  fetchImpl?: ArtifactsFetch;
}

export interface ArtifactsClient {
  listWorkspaceFiles(
    projectId: string,
    workspaceId: string,
    path?: string,
  ): Promise<WorkspaceListingView>;
  workspaceFileContentUrl(projectId: string, workspaceId: string, path: string): string;
  /** 兼容任务书命名：与 workspaceFileContentUrl 相同。 */
  workspaceFileUrl(projectId: string, workspaceId: string, path: string): string;
  publishArtifact(projectId: string, request: PublishArtifactRequest): Promise<ArtifactView>;
  listArtifacts(projectId: string): Promise<ArtifactView[]>;
  artifactContentUrl(projectId: string, artifactId: string): string;
  /** 带鉴权的字节读取，供预览（object URL）使用。 */
  readWorkspaceFile(projectId: string, workspaceId: string, path: string): Promise<ArtifactBytes>;
  readArtifact(projectId: string, artifactId: string): Promise<ArtifactBytes>;
}

function defaultBaseUrl(): string {
  try {
    return useConnectionStore().httpBaseUrl;
  } catch {
    return "";
  }
}

function defaultToken(): string | undefined {
  try {
    return useConnectionStore().apiToken;
  } catch {
    return undefined;
  }
}

function defaultFetch(): ArtifactsFetch {
  const impl = (globalThis as { fetch?: ArtifactsFetch }).fetch;
  if (!impl) throw new HttpError({ code: "internal", status: 0, message: "fetch 不可用" });
  return impl.bind(globalThis) as ArtifactsFetch;
}

function requireSegment(value: string, what: string): string {
  if (!/^[A-Za-z0-9][A-Za-z0-9_-]{0,127}$/.test(value)) {
    throw new HttpError({ code: "invalid_request", status: 0, message: `非法${what}：${value}` });
  }
  return encodeURIComponent(value);
}

function projectBase(projectId: string): string {
  return `/api/v1/projects/${requireSegment(projectId, "项目 id")}`;
}

function encodeFilePath(path: string): string {
  return encodeURIComponent(path.replace(/^\/+/, ""));
}

function parseArtifactSource(raw: unknown): ArtifactSourceView | null {
  if (raw === null || typeof raw !== "object" || Array.isArray(raw)) return null;
  const value = raw as Record<string, unknown>;
  if (typeof value.workspace_id !== "string" || typeof value.path !== "string") return null;
  return {
    workspace_id: value.workspace_id,
    path: value.path,
    agent_id: typeof value.agent_id === "string" ? value.agent_id : null,
  };
}

function parseArtifact(raw: unknown): ArtifactView | null {
  if (raw === null || typeof raw !== "object" || Array.isArray(raw)) return null;
  const value = raw as Record<string, unknown>;
  const source = parseArtifactSource(value.source);
  if (
    typeof value.artifact_id !== "string" ||
    typeof value.filename !== "string" ||
    typeof value.mime_type !== "string" ||
    typeof value.size !== "number" ||
    typeof value.published_at !== "string" ||
    source === null
  ) {
    return null;
  }
  return {
    artifact_id: value.artifact_id,
    filename: value.filename,
    mime_type: value.mime_type,
    size: value.size,
    title: typeof value.title === "string" ? value.title : null,
    published_at: value.published_at,
    source,
  };
}

function parseEntry(raw: unknown): WorkspaceEntryView | null {
  if (raw === null || typeof raw !== "object" || Array.isArray(raw)) return null;
  const value = raw as Record<string, unknown>;
  if (
    typeof value.name !== "string" ||
    typeof value.path !== "string" ||
    (value.type !== "file" && value.type !== "dir")
  ) {
    return null;
  }
  return {
    name: value.name,
    path: value.path,
    type: value.type,
    size: typeof value.size === "number" ? value.size : null,
    mtime: typeof value.mtime === "string" ? value.mtime : null,
  };
}

function parseListing(raw: unknown): WorkspaceListingView {
  if (raw === null || typeof raw !== "object" || Array.isArray(raw)) {
    throw new HttpError({ code: "internal", status: 0, message: "工作区列表响应形状非法" });
  }
  const value = raw as Record<string, unknown>;
  if (typeof value.path !== "string" || !Array.isArray(value.entries)) {
    throw new HttpError({
      code: "internal",
      status: 0,
      message: "工作区列表响应缺少 path/entries",
    });
  }
  const entries: WorkspaceEntryView[] = [];
  for (const item of value.entries) {
    const entry = parseEntry(item);
    if (entry) entries.push(entry);
  }
  return { path: value.path, entries };
}

function parseArtifactList(raw: unknown): ArtifactView[] {
  if (raw === null || typeof raw !== "object" || Array.isArray(raw)) {
    throw new HttpError({ code: "internal", status: 0, message: "产物列表响应形状非法" });
  }
  const value = raw as Record<string, unknown>;
  if (!Array.isArray(value.artifacts)) {
    throw new HttpError({ code: "internal", status: 0, message: "产物列表响应缺少 artifacts" });
  }
  const artifacts: ArtifactView[] = [];
  for (const item of value.artifacts) {
    const artifact = parseArtifact(item);
    if (artifact) artifacts.push(artifact);
  }
  return artifacts;
}

function parseArtifactOne(raw: unknown): ArtifactView {
  const artifact = parseArtifact(raw);
  if (!artifact) {
    throw new HttpError({ code: "internal", status: 0, message: "发布产物响应形状非法" });
  }
  return artifact;
}

function errorFromResponse(status: number, body: string): HttpError {
  let code = STATUS_CODES[status] ?? "internal";
  let message = `产物请求失败（HTTP ${status}）`;
  let detail: unknown;
  if (body) {
    try {
      const parsed = JSON.parse(body) as {
        error?: { code?: unknown; message?: unknown; detail?: unknown };
      };
      const part = parsed.error;
      if (part && typeof part.code === "string" && part.code) code = part.code;
      if (part && typeof part.message === "string" && part.message) message = part.message;
      detail = part?.detail;
    } catch {
      // 非 JSON 错误体：保留状态码兜底文案。
    }
  }
  return new HttpError({ code, status, message, detail });
}

export function createArtifactsClient(options: ArtifactsClientOptions = {}): ArtifactsClient {
  const fetchImpl = options.fetchImpl ?? defaultFetch();
  const baseUrl = () => (options.baseUrl ?? defaultBaseUrl()).replace(/\/$/, "");
  const token = () => options.token ?? defaultToken();

  async function request(
    path: string,
    init: { method: "GET" | "POST"; body?: unknown; timeoutMs: number },
  ): Promise<{ response: ArtifactsFetchResponse; text: string }> {
    const controllers = (
      globalThis as { AbortController?: new () => { signal: HttpAbortSignal; abort(): void } }
    ).AbortController;
    const controller = controllers ? new controllers() : null;
    const timers = globalThis as {
      setTimeout?: (handler: () => void, ms: number) => unknown;
      clearTimeout?: (handle: unknown) => void;
    };
    let timedOut = false;
    const timer = timers.setTimeout?.(() => {
      timedOut = true;
      controller?.abort();
    }, init.timeoutMs);
    const headers: Record<string, string> = { Accept: "application/json" };
    const auth = token();
    if (auth) headers.Authorization = `Bearer ${auth}`;
    if (init.body !== undefined) headers["Content-Type"] = "application/json";
    try {
      const response = await fetchImpl(`${baseUrl()}${path}`, {
        method: init.method,
        headers,
        ...(init.body !== undefined ? { body: JSON.stringify(init.body) } : {}),
        ...(controller ? { signal: controller.signal } : {}),
      });
      const text = await response.text();
      if (!response.ok) throw errorFromResponse(response.status, text);
      return { response, text };
    } catch (error) {
      if (error instanceof HttpError) throw error;
      if (timedOut) {
        throw new HttpError({ code: "unavailable", status: 0, message: "产物请求超时" });
      }
      throw new HttpError({
        code: "unavailable",
        status: 0,
        message: `产物请求失败：${error instanceof Error ? error.message : String(error)}`,
      });
    } finally {
      if (timer !== undefined) timers.clearTimeout?.(timer);
    }
  }

  async function requestJson(path: string, init: { method: "GET" | "POST"; body?: unknown }) {
    const { response, text } = await request(path, { ...init, timeoutMs: DEFAULT_JSON_TIMEOUT_MS });
    const contentType = response.headers.get("content-type") ?? "";
    if (contentType.includes("application/json")) {
      try {
        return JSON.parse(text) as unknown;
      } catch {
        throw new HttpError({ code: "internal", status: 0, message: "产物响应不是合法 JSON" });
      }
    }
    try {
      return JSON.parse(text) as unknown;
    } catch {
      throw new HttpError({ code: "internal", status: 0, message: "产物响应不是合法 JSON" });
    }
  }

  async function requestBytes(path: string): Promise<ArtifactBytes> {
    const controllers = (
      globalThis as { AbortController?: new () => { signal: HttpAbortSignal; abort(): void } }
    ).AbortController;
    const controller = controllers ? new controllers() : null;
    const timers = globalThis as {
      setTimeout?: (handler: () => void, ms: number) => unknown;
      clearTimeout?: (handle: unknown) => void;
    };
    let timedOut = false;
    const timer = timers.setTimeout?.(() => {
      timedOut = true;
      controller?.abort();
    }, DEFAULT_BYTES_TIMEOUT_MS);
    const headers: Record<string, string> = {};
    const auth = token();
    if (auth) headers.Authorization = `Bearer ${auth}`;
    try {
      const response = await fetchImpl(`${baseUrl()}${path}`, {
        method: "GET",
        headers,
        ...(controller ? { signal: controller.signal } : {}),
      });
      if (!response.ok) {
        const text = await response.text();
        throw errorFromResponse(response.status, text);
      }
      const buffer = await response.arrayBuffer();
      return {
        bytes: new Uint8Array(buffer),
        mimeType: response.headers.get("content-type") ?? "application/octet-stream",
      };
    } catch (error) {
      if (error instanceof HttpError) throw error;
      if (timedOut) {
        throw new HttpError({ code: "unavailable", status: 0, message: "读取产物超时" });
      }
      throw new HttpError({
        code: "unavailable",
        status: 0,
        message: `读取产物失败：${error instanceof Error ? error.message : String(error)}`,
      });
    } finally {
      if (timer !== undefined) timers.clearTimeout?.(timer);
    }
  }

  const workspacePath = (projectId: string, workspaceId: string): string =>
    `${projectBase(projectId)}/workspaces/${requireSegment(workspaceId, "工作区 id")}`;

  return {
    async listWorkspaceFiles(projectId, workspaceId, path = "") {
      const query = `path=${encodeFilePath(path)}`;
      const raw = await requestJson(`${workspacePath(projectId, workspaceId)}/files?${query}`, {
        method: "GET",
      });
      return parseListing(raw);
    },
    workspaceFileContentUrl(projectId, workspaceId, path) {
      return `${baseUrl()}${workspacePath(projectId, workspaceId)}/file?path=${encodeFilePath(path)}`;
    },
    workspaceFileUrl(projectId, workspaceId, path) {
      return `${baseUrl()}${workspacePath(projectId, workspaceId)}/file?path=${encodeFilePath(path)}`;
    },
    async publishArtifact(projectId, requestBody) {
      const raw = await requestJson(`${projectBase(projectId)}/artifacts`, {
        method: "POST",
        body: {
          workspace_id: requestBody.workspace_id,
          path: requestBody.path,
          ...(requestBody.mime_type ? { mime_type: requestBody.mime_type } : {}),
          ...(requestBody.title ? { title: requestBody.title } : {}),
        },
      });
      return parseArtifactOne(raw);
    },
    async listArtifacts(projectId) {
      const raw = await requestJson(`${projectBase(projectId)}/artifacts`, { method: "GET" });
      return parseArtifactList(raw);
    },
    artifactContentUrl(projectId, artifactId) {
      return `${baseUrl()}${projectBase(projectId)}/artifacts/${requireSegment(artifactId, "产物 id")}/content`;
    },
    async readWorkspaceFile(projectId, workspaceId, path) {
      return requestBytes(
        `${workspacePath(projectId, workspaceId)}/file?path=${encodeFilePath(path)}`,
      );
    },
    async readArtifact(projectId, artifactId) {
      return requestBytes(
        `${projectBase(projectId)}/artifacts/${requireSegment(artifactId, "产物 id")}/content`,
      );
    },
  };
}

let shared: ArtifactsClient | null = null;

export function artifactsClient(): ArtifactsClient {
  shared ??= createArtifactsClient();
  return shared;
}

export function setArtifactsClientForTest(client: ArtifactsClient | null): void {
  shared = client;
}

export function listWorkspaceFiles(
  projectId: string,
  workspaceId: string,
  path?: string,
): Promise<WorkspaceListingView> {
  return artifactsClient().listWorkspaceFiles(projectId, workspaceId, path);
}

export function workspaceFileContentUrl(
  projectId: string,
  workspaceId: string,
  path: string,
): string {
  return artifactsClient().workspaceFileContentUrl(projectId, workspaceId, path);
}

export function workspaceFileUrl(projectId: string, workspaceId: string, path: string): string {
  return artifactsClient().workspaceFileUrl(projectId, workspaceId, path);
}

export function publishArtifact(
  projectId: string,
  request: PublishArtifactRequest,
): Promise<ArtifactView> {
  return artifactsClient().publishArtifact(projectId, request);
}

export function listArtifacts(projectId: string): Promise<ArtifactView[]> {
  return artifactsClient().listArtifacts(projectId);
}

export function artifactContentUrl(projectId: string, artifactId: string): string {
  return artifactsClient().artifactContentUrl(projectId, artifactId);
}

export function readWorkspaceFile(
  projectId: string,
  workspaceId: string,
  path: string,
): Promise<ArtifactBytes> {
  return artifactsClient().readWorkspaceFile(projectId, workspaceId, path);
}

export function readArtifact(projectId: string, artifactId: string): Promise<ArtifactBytes> {
  return artifactsClient().readArtifact(projectId, artifactId);
}
