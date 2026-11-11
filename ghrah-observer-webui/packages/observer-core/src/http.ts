/** 观察端 HTTP 管理面客户端：base URL 派生、超时、错误归一与只读重试。
 *
 * 这里刻意不依赖 DOM lib：observer-core 作为库同时服务浏览器与 Node 测试，
 * 只声明所需的最小 fetch/abort 形状。
 */

export type HttpQueryValue =
  string | number | boolean | null | undefined | readonly (string | number)[];

export type HttpQuery = Readonly<Record<string, HttpQueryValue>>;

/** 最小 URL 形状（ES lib 不含 DOM 的 URL，浏览器与 Node 均满足）。 */
export interface HttpUrl {
  readonly protocol: string;
  readonly host: string;
  readonly searchParams: { append(name: string, value: string): void };
  toString(): string;
}

type HttpUrlCtor = new (input: string, base?: string) => HttpUrl;

function globalUrl(): HttpUrlCtor {
  const ctor = (globalThis as { URL?: HttpUrlCtor }).URL;
  if (!ctor) throw new HttpError({ code: "internal", status: 0, message: "URL 不可用" });
  return ctor;
}

/** 最小 AbortSignal 形状（浏览器与 Node 的 AbortSignal 均满足）。 */
export interface HttpAbortSignal {
  readonly aborted: boolean;
  addEventListener(type: "abort", listener: () => void): void;
}

/** 最小 fetch 初始化形状。 */
export interface HttpFetchInit {
  method: string;
  headers: Record<string, string>;
  body?: string;
  signal: HttpAbortSignal;
}

/** 最小 fetch 响应形状。 */
export interface HttpFetchResponse {
  readonly ok: boolean;
  readonly status: number;
  readonly headers: { get(name: string): string | null };
  json(): Promise<unknown>;
  text(): Promise<string>;
}

export type HttpFetch = (url: string, init: HttpFetchInit) => Promise<HttpFetchResponse>;

export interface HttpRequestOptions {
  query?: HttpQuery;
  body?: unknown;
  signal?: HttpAbortSignal;
  timeoutMs?: number;
}

export interface HttpClientOptions {
  /** 形如 http(s)://host[:port]；末尾斜杠会归一化。 */
  baseUrl: string;
  /** 非回环入口的 Bearer token；回环部署可省略。 */
  token?: string;
  fetchImpl?: HttpFetch;
  timeoutMs?: number;
}

export interface HttpErrorInit {
  code: string;
  status: number;
  message: string;
  detail?: unknown;
}

interface AbortControllerLike {
  readonly signal: HttpAbortSignal;
  abort(): void;
}

interface AbortControllerCtor {
  new (): AbortControllerLike;
}

/** 管理面统一错误；code 来自服务端错误体或 HTTP 状态兜底。 */
export class HttpError extends Error {
  readonly code: string;
  readonly status: number;
  readonly detail: unknown;

  constructor(init: HttpErrorInit) {
    super(init.message);
    this.name = "HttpError";
    this.code = init.code;
    this.status = init.status;
    this.detail = init.detail;
  }
}

const DEFAULT_TIMEOUT_MS = 15_000;
const WRITE_TIMEOUT_MS = 30_000;

const STATUS_CODES: Readonly<Record<number, string>> = {
  400: "invalid_request",
  401: "access_denied",
  403: "access_denied",
  404: "not_found",
  409: "conflict",
  422: "invalid_request",
  503: "unavailable",
};

/** 从 WS 地址派生 HTTP base：ws(s) + 去掉 /ws 路径只保留 origin。 */
export function deriveHttpBase(wsUrl: string): string {
  const Url = globalUrl();
  const parsed = new Url(wsUrl);
  const scheme = parsed.protocol === "wss:" ? "https:" : "http:";
  return `${scheme}//${parsed.host}`;
}

function buildUrl(baseUrl: string, path: string, query?: HttpQuery): string {
  const base = baseUrl.endsWith("/") ? baseUrl.slice(0, -1) : baseUrl;
  const Url = globalUrl();
  const url = new Url(`${base}${path.startsWith("/") ? path : `/${path}`}`);
  if (query) {
    for (const [key, value] of Object.entries(query)) {
      if (value === undefined || value === null) continue;
      if (Array.isArray(value)) {
        for (const item of value) url.searchParams.append(key, String(item));
      } else {
        url.searchParams.append(key, String(value));
      }
    }
  }
  return url.toString();
}

function isAbortError(error: unknown): boolean {
  return (error as { name?: unknown } | undefined)?.name === "AbortError";
}

function networkError(error: unknown, timeout: boolean): HttpError {
  const message = timeout
    ? "管理面请求超时，请确认服务端可达或稍后重试"
    : `管理面请求失败：${error instanceof Error ? error.message : String(error)}`;
  return new HttpError({ code: "unavailable", status: 0, message });
}

async function parseErrorResponse(response: HttpFetchResponse): Promise<HttpError> {
  const fallbackCode = STATUS_CODES[response.status] ?? "internal";
  let body: unknown;
  try {
    body = await response.json();
  } catch {
    body = undefined;
  }
  const envelope = body as { error?: { code?: unknown; message?: unknown; detail?: unknown } };
  const errorPart = envelope.error;
  const code =
    typeof errorPart?.code === "string" && errorPart.code.length > 0
      ? errorPart.code
      : fallbackCode;
  const message =
    typeof errorPart?.message === "string" && errorPart.message.length > 0
      ? errorPart.message
      : `管理面请求失败（HTTP ${response.status}）`;
  return new HttpError({ code, status: response.status, message, detail: errorPart?.detail });
}

function globalAbortController(): AbortControllerCtor {
  const ctor = (globalThis as { AbortController?: AbortControllerCtor }).AbortController;
  if (!ctor) {
    throw new HttpError({ code: "internal", status: 0, message: "AbortController 不可用" });
  }
  return ctor;
}

function defaultFetch(): HttpFetch {
  const fetchImpl = (globalThis as { fetch?: HttpFetch }).fetch;
  if (!fetchImpl) throw new HttpError({ code: "internal", status: 0, message: "fetch 不可用" });
  return fetchImpl.bind(globalThis) as HttpFetch;
}

export class HttpClient {
  private readonly baseUrl: string;
  private readonly token: string | undefined;
  private readonly fetchImpl: HttpFetch;
  private readonly timeoutMs: number;

  constructor(options: HttpClientOptions) {
    this.baseUrl = options.baseUrl;
    this.token = options.token;
    this.fetchImpl = options.fetchImpl ?? defaultFetch();
    this.timeoutMs = options.timeoutMs ?? DEFAULT_TIMEOUT_MS;
  }

  async get<T>(path: string, options: HttpRequestOptions = {}): Promise<T> {
    return this.request<T>("GET", path, options, true);
  }

  async post<T>(path: string, body: unknown, options: HttpRequestOptions = {}): Promise<T> {
    return this.request<T>("POST", path, { ...options, body }, false);
  }

  async put<T>(path: string, body: unknown, options: HttpRequestOptions = {}): Promise<T> {
    return this.request<T>("PUT", path, { ...options, body }, false);
  }

  async patch<T>(path: string, body: unknown, options: HttpRequestOptions = {}): Promise<T> {
    return this.request<T>("PATCH", path, { ...options, body }, false);
  }

  async delete<T>(path: string, options: HttpRequestOptions = {}): Promise<T> {
    return this.request<T>("DELETE", path, options, false);
  }

  private async request<T>(
    method: "GET" | "POST" | "PUT" | "PATCH" | "DELETE",
    path: string,
    options: HttpRequestOptions,
    retryable: boolean,
  ): Promise<T> {
    const attempts = retryable ? 2 : 1;
    for (let attempt = 0; attempt < attempts; attempt += 1) {
      try {
        return await this.requestOnce<T>(method, path, options);
      } catch (error) {
        if (error instanceof HttpError) throw error;
        if (retryable && attempt < attempts - 1) continue;
        throw networkError(error, false);
      }
    }
    throw networkError(undefined, false);
  }

  private async requestOnce<T>(
    method: "GET" | "POST" | "PUT" | "PATCH" | "DELETE",
    path: string,
    options: HttpRequestOptions,
  ): Promise<T> {
    const timeoutMs = options.timeoutMs ?? (method === "GET" ? this.timeoutMs : WRITE_TIMEOUT_MS);
    const Controller = globalAbortController();
    const controller = new Controller();
    const timers = globalThis as {
      setTimeout?: (handler: () => void, ms: number) => unknown;
      clearTimeout?: (handle: unknown) => void;
    };
    let timedOut = false;
    const timer = timers.setTimeout?.(() => {
      timedOut = true;
      controller.abort();
    }, timeoutMs);
    if (options.signal?.aborted) controller.abort();
    options.signal?.addEventListener("abort", () => controller.abort());
    try {
      const headers: Record<string, string> = { Accept: "application/json" };
      if (this.token) headers.Authorization = `Bearer ${this.token}`;
      if (options.body !== undefined) headers["Content-Type"] = "application/json";
      const response = await this.fetchImpl(buildUrl(this.baseUrl, path, options.query), {
        method,
        headers,
        body: options.body === undefined ? undefined : JSON.stringify(options.body),
        signal: controller.signal,
      });
      if (!response.ok) throw await parseErrorResponse(response);
      if (response.status === 204) return undefined as T;
      const contentType = response.headers.get("content-type") ?? "";
      if (contentType.includes("application/json")) {
        return (await response.json()) as T;
      }
      return (await response.text()) as T;
    } catch (error) {
      if (error instanceof HttpError) throw error;
      if (timedOut) throw networkError(error, true);
      if (options.signal?.aborted === true) throw networkError(error, false);
      if (isAbortError(error)) throw networkError(error, true);
      throw error;
    } finally {
      if (timer !== undefined) timers.clearTimeout?.(timer);
    }
  }
}
