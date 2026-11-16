// SPDX-FileCopyrightText: 2026 chenxya <chenxya@ghrah.org>
//
// SPDX-License-Identifier: Apache-2.0

/** 附件字节上传客户端：JSON 管理面之外的独立通道（XHR，因为需要上传进度）。 */

import { HttpError, type HttpAbortSignal } from "./http.js";
import { useConnectionStore } from "./stores/connection.js";

/** 默认上传超时（视频可能很大；与 JSON 管理面 30 s 区分）。 */
export const DEFAULT_ATTACHMENT_TIMEOUT_MS = 300_000;

/** 附件上传单文件上限（Subject 侧同源，配置可调）。 */
export const MAX_ATTACHMENT_BYTES = 64 * 1024 * 1024;

const UPLOAD_STATUS_CODES: Readonly<Record<number, string>> = {
  400: "invalid_request",
  403: "access_denied",
  404: "attachment_not_found",
  413: "attachment_too_large",
  415: "unsupported_attachment_type",
  416: "range_not_satisfiable",
  503: "media_disabled",
};

export interface AttachmentUploadProgress {
  loaded: number;
  total: number;
}

export interface UploadAttachmentOptions {
  mimeType: string;
  filename?: string | null;
  onProgress?: (progress: AttachmentUploadProgress) => void;
  /** 端点级超时覆写；缺省 {@link DEFAULT_ATTACHMENT_TIMEOUT_MS}。 */
  timeoutMs?: number;
  signal?: HttpAbortSignal;
}

export interface UploadedAttachment {
  attachmentId: string;
  size: number;
  mimeType: string;
  filename: string | null;
}

/** 最小 Blob 形状（observer-core 不依赖 DOM lib；浏览器 File/Blob 结构兼容）。 */
export interface UploadBlobLike {
  readonly size: number;
  readonly type: string;
}

/** 观察端使用的上传体：原始字节/Blob，不经过 JSON 编码。 */
export type UploadBody = ArrayBuffer | Uint8Array | UploadBlobLike;

/** XHR 的最小结构（observer-core 不依赖 DOM lib，便于 Node 测试注入）。 */
export interface UploadXhrProgressEvent {
  loaded: number;
  total: number;
}

export interface UploadXhr {
  upload: {
    addEventListener(type: "progress", listener: (event: UploadXhrProgressEvent) => void): void;
  };
  addEventListener(type: "load" | "error" | "abort" | "timeout", listener: () => void): void;
  open(method: string, url: string): void;
  setRequestHeader(name: string, value: string): void;
  send(body: UploadBody): void;
  abort(): void;
  status: number;
  responseText: string;
  timeout: number;
}

export interface AttachmentClientOptions {
  baseUrl?: string;
  token?: string;
  xhrFactory?: () => UploadXhr;
  timeoutMs?: number;
}

export interface AttachmentClient {
  upload(
    projectId: string,
    bytes: UploadBody,
    options: UploadAttachmentOptions,
  ): Promise<UploadedAttachment>;
  url(projectId: string, attachmentId: string): string;
}

function defaultXhrFactory(): UploadXhr {
  const ctor = (globalThis as { XMLHttpRequest?: new () => UploadXhr }).XMLHttpRequest;
  if (!ctor) throw new HttpError({ code: "internal", status: 0, message: "XHR 不可用" });
  return new ctor();
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

function requireSegment(value: string, what: string): string {
  if (!/^[A-Za-z0-9][A-Za-z0-9_-]{0,127}$/.test(value)) {
    throw new HttpError({ code: "invalid_request", status: 0, message: `非法${what}：${value}` });
  }
  return encodeURIComponent(value);
}

export function attachmentPath(projectId: string, attachmentId?: string): string {
  const project = requireSegment(projectId, "项目 id");
  const base = `/api/v1/projects/${project}/attachments`;
  return attachmentId === undefined ? base : `${base}/${requireSegment(attachmentId, "附件 id")}`;
}

function parseUploadError(status: number, body: string): HttpError {
  let code = UPLOAD_STATUS_CODES[status] ?? "internal";
  let message = status === 0 ? "附件上传失败" : `附件上传失败（HTTP ${status}）`;
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

export function createAttachmentClient(options: AttachmentClientOptions = {}): AttachmentClient {
  const defaultTimeout = options.timeoutMs ?? DEFAULT_ATTACHMENT_TIMEOUT_MS;
  const xhrFactory = options.xhrFactory ?? defaultXhrFactory;
  const baseUrl = () => (options.baseUrl ?? defaultBaseUrl()).replace(/\/$/, "");
  const token = () => options.token ?? defaultToken();

  function upload(
    projectId: string,
    bytes: UploadBody,
    uploadOptions: UploadAttachmentOptions,
  ): Promise<UploadedAttachment> {
    return new Promise<UploadedAttachment>((resolve, reject) => {
      let settled = false;
      const finish = (action: () => void) => {
        if (settled) return;
        settled = true;
        action();
      };
      let xhr: UploadXhr;
      try {
        xhr = xhrFactory();
      } catch (error) {
        reject(error);
        return;
      }
      const url = `${baseUrl()}${attachmentPath(projectId)}`;
      xhr.open("POST", url);
      xhr.setRequestHeader("Content-Type", uploadOptions.mimeType);
      if (uploadOptions.filename) {
        xhr.setRequestHeader("X-Filename", encodeURIComponent(uploadOptions.filename));
      }
      const auth = token();
      if (auth) xhr.setRequestHeader("Authorization", `Bearer ${auth}`);
      xhr.timeout = uploadOptions.timeoutMs ?? defaultTimeout;
      if (uploadOptions.onProgress) {
        xhr.upload.addEventListener("progress", (event) => {
          if (!settled) uploadOptions.onProgress?.({ loaded: event.loaded, total: event.total });
        });
      }
      xhr.addEventListener("load", () => {
        if (xhr.status < 200 || xhr.status >= 300) {
          finish(() => reject(parseUploadError(xhr.status, xhr.responseText)));
          return;
        }
        let parsed: unknown;
        try {
          parsed = JSON.parse(xhr.responseText);
        } catch {
          finish(() =>
            reject(
              new HttpError({ code: "internal", status: xhr.status, message: "附件响应不是 JSON" }),
            ),
          );
          return;
        }
        const view = parsed as {
          attachment_id?: unknown;
          size?: unknown;
          mime_type?: unknown;
          filename?: unknown;
        };
        if (typeof view.attachment_id !== "string" || !view.attachment_id) {
          finish(() =>
            reject(
              new HttpError({
                code: "internal",
                status: xhr.status,
                message: "附件响应缺少 attachment_id",
              }),
            ),
          );
          return;
        }
        const attachmentId = view.attachment_id;
        finish(() =>
          resolve({
            attachmentId,
            size: typeof view.size === "number" ? view.size : 0,
            mimeType: typeof view.mime_type === "string" ? view.mime_type : uploadOptions.mimeType,
            filename:
              typeof view.filename === "string" ? view.filename : (uploadOptions.filename ?? null),
          }),
        );
      });
      xhr.addEventListener("error", () =>
        finish(() =>
          reject(new HttpError({ code: "unavailable", status: 0, message: "附件上传网络失败" })),
        ),
      );
      xhr.addEventListener("timeout", () =>
        finish(() =>
          reject(new HttpError({ code: "timeout", status: 0, message: "附件上传超时" })),
        ),
      );
      xhr.addEventListener("abort", () =>
        finish(() =>
          reject(new HttpError({ code: "aborted", status: 0, message: "附件上传已取消" })),
        ),
      );
      if (uploadOptions.signal?.aborted) {
        finish(() =>
          reject(new HttpError({ code: "aborted", status: 0, message: "附件上传已取消" })),
        );
        return;
      }
      uploadOptions.signal?.addEventListener("abort", () => xhr.abort());
      xhr.send(bytes);
    });
  }

  return {
    upload,
    url(projectId: string, attachmentId: string): string {
      return `${baseUrl()}${attachmentPath(projectId, attachmentId)}`;
    },
  };
}

let shared: AttachmentClient | null = null;

/** 进程内默认客户端；连接地址/token 在每次调用时读取，不做缓存。 */
export function attachmentClient(): AttachmentClient {
  shared ??= createAttachmentClient();
  return shared;
}

export function setAttachmentClientForTest(client: AttachmentClient | null): void {
  shared = client;
}

export function uploadAttachment(
  projectId: string,
  bytes: UploadBody,
  options: UploadAttachmentOptions,
): Promise<UploadedAttachment> {
  return attachmentClient().upload(projectId, bytes, options);
}

export function attachmentUrl(projectId: string, attachmentId: string): string {
  return attachmentClient().url(projectId, attachmentId);
}
