import {
  RuntimeAbilitiesViewSchema,
  RuntimeAbilityCatalogSchema,
  RuntimeAbilityChangesSchema,
  RuntimeAbilityInstanceCreateSchema,
  RuntimeAbilityInstanceSchema,
  RuntimeAgentCreateSchema,
  RuntimeAgentIdSchema,
  RuntimeAgentPatchSchema,
  RuntimeAgentResourceSchema,
  RuntimeAgentsPageSchema,
  RuntimeDeliveryIdSchema,
  RuntimeDeliveryViewSchema,
  RuntimeGrantRequestSchema,
  RuntimeMessageAcceptedSchema,
  RuntimeMessageCreateSchema,
  RuntimeMetaSchema,
  RuntimeNodeIdSchema,
  RuntimeNodeViewSchema,
  RuntimeProblemSchema,
  RuntimeStandardProblemSchema,
  RuntimeStateTransitionResultSchema,
  RuntimeSystemStatusSchema,
  RuntimeTimelinePageSchema,
  RuntimeToolRequestSchema,
  type RuntimeAgentCreate,
  type RuntimeAgentPatch,
  type RuntimeAbilityInstanceCreate,
  type RuntimeGrantRequest,
  type RuntimeMessageCreate,
  type RuntimeNodeView,
  type RuntimeToolRequest,
} from "@ghrah/protocol";
import type { HttpAbortSignal, HttpFetch } from "./http.js";

export interface RuntimeResult<T> {
  status: number;
  body: T;
  etag: string | null;
  location: string | null;
  replayed: boolean;
}

export class RuntimeApiError extends Error {
  constructor(
    readonly status: number,
    readonly code: string,
    readonly problem: unknown,
  ) {
    super(`${status} ${code}`);
    this.name = "RuntimeApiError";
  }
}

export interface RuntimeObserverOptions {
  baseUrl: string;
  token?: string;
  fetchImpl?: HttpFetch;
  timeoutMs?: number;
}

interface RuntimeController {
  signal: HttpAbortSignal;
  abort(): void;
}

export class RuntimeObserverClient {
  private readonly baseUrl: string;
  private readonly fetchImpl: HttpFetch;

  constructor(private readonly options: RuntimeObserverOptions) {
    this.baseUrl = options.baseUrl.replace(/\/+$/, "");
    if (!/^https?:\/\//.test(this.baseUrl)) throw new Error("Runtime HTTP URL required");
    const implementation = options.fetchImpl ?? (globalThis as { fetch?: HttpFetch }).fetch;
    if (!implementation) throw new Error("Runtime fetch is unavailable");
    this.fetchImpl = implementation.bind(globalThis);
  }

  private async request<T>(
    method: string,
    path: string,
    schema: { parse(value: unknown): T } | null,
    body?: unknown,
    headers: Record<string, string> = {},
    signal?: HttpAbortSignal,
  ): Promise<RuntimeResult<T>> {
    const Controller = (globalThis as unknown as { AbortController: new () => RuntimeController })
      .AbortController;
    const controller = new Controller();
    const abort = () => controller.abort();
    if (signal?.aborted) controller.abort();
    signal?.addEventListener("abort", abort);
    const timers = globalThis as unknown as {
      setTimeout(handler: () => void, ms: number): unknown;
      clearTimeout(timer: unknown): void;
    };
    const timer = timers.setTimeout(abort, this.options.timeoutMs ?? 45_000);
    try {
      const response = await this.fetchImpl(this.baseUrl + path, {
        method,
        headers: {
          Accept: "application/json",
          ...(body === undefined ? {} : { "Content-Type": "application/json" }),
          ...(this.options.token ? { Authorization: `Bearer ${this.options.token}` } : {}),
          ...headers,
        },
        body: body === undefined ? undefined : JSON.stringify(body),
        signal: controller.signal,
      });
      const empty =
        response.status === 204 || (response.status === 304 && path.startsWith("/v1/nodes/"));
      const payload = empty ? null : await response.json();
      if (!response.ok && !empty) {
        const domain = RuntimeProblemSchema.safeParse(payload);
        const standard = RuntimeStandardProblemSchema.safeParse(payload);
        if (!domain.success && !standard.success)
          throw new RuntimeApiError(response.status, "INVALID_RESPONSE", payload);
        throw new RuntimeApiError(
          response.status,
          domain.success ? domain.data.code : `HTTP_${response.status}`,
          payload,
        );
      }
      return {
        status: response.status,
        body: empty ? (null as T) : schema ? schema.parse(payload) : (payload as T),
        etag: response.headers.get("etag"),
        location: response.headers.get("location"),
        replayed: response.headers.get("idempotency-replayed") === "true",
      };
    } catch (error) {
      if (error instanceof RuntimeApiError) throw error;
      throw new RuntimeApiError(
        0,
        controller.signal.aborted ? "CANCELLED_OR_TIMEOUT" : "UNAVAILABLE_OR_INVALID_RESPONSE",
        error,
      );
    } finally {
      timers.clearTimeout(timer);
      (
        signal as
          | (HttpAbortSignal & {
              removeEventListener?: (type: string, listener: () => void) => void;
            })
          | undefined
      )?.removeEventListener?.("abort", abort);
    }
  }

  status() {
    return this.request("GET", "/v1/status", RuntimeSystemStatusSchema);
  }
  meta() {
    return this.request("GET", "/v1/meta", RuntimeMetaSchema);
  }
  listAgents(cursor?: string, limit = 50) {
    return this.request(
      "GET",
      `/v1/agents?limit=${limit}${cursor ? `&cursor=${encodeURIComponent(cursor)}` : ""}`,
      RuntimeAgentsPageSchema,
    );
  }
  agent(agentId: string) {
    return this.request(
      "GET",
      `/v1/agents/${RuntimeAgentIdSchema.parse(agentId)}`,
      RuntimeAgentResourceSchema,
    );
  }
  createAgent(input: RuntimeAgentCreate, key: string) {
    return this.request(
      "POST",
      "/v1/agents",
      RuntimeAgentResourceSchema,
      RuntimeAgentCreateSchema.parse(input),
      { "Idempotency-Key": key },
    );
  }
  replaceConfig(agentId: string, input: RuntimeAgentPatch, etag: string) {
    return this.request(
      "PATCH",
      `/v1/agents/${RuntimeAgentIdSchema.parse(agentId)}`,
      RuntimeAgentResourceSchema,
      RuntimeAgentPatchSchema.parse(input),
      { "If-Match": etag },
    );
  }
  deleteAgent(agentId: string, etag: string) {
    return this.request<null>(
      "DELETE",
      `/v1/agents/${RuntimeAgentIdSchema.parse(agentId)}`,
      null,
      undefined,
      { "If-Match": etag },
    );
  }
  setState(agentId: string, state: "running" | "stopped", etag: string) {
    return this.request(
      "PUT",
      `/v1/agents/${RuntimeAgentIdSchema.parse(agentId)}/state`,
      RuntimeStateTransitionResultSchema,
      { state },
      { "If-Match": etag },
    );
  }
  submitMessage(agentId: string, input: RuntimeMessageCreate, key: string) {
    return this.request(
      "POST",
      `/v1/agents/${RuntimeAgentIdSchema.parse(agentId)}/messages`,
      RuntimeMessageAcceptedSchema,
      RuntimeMessageCreateSchema.parse(input),
      { "Idempotency-Key": key },
    );
  }
  delivery(deliveryId: string, wait = 0, signal?: HttpAbortSignal) {
    return this.request(
      "GET",
      `/v1/deliveries/${RuntimeDeliveryIdSchema.parse(deliveryId)}?wait=${wait}`,
      RuntimeDeliveryViewSchema,
      undefined,
      {},
      signal,
    );
  }
  timeline(agentId: string, cursor?: string, limit = 100) {
    return this.request(
      "GET",
      `/v1/agents/${RuntimeAgentIdSchema.parse(agentId)}/timeline?limit=${limit}${cursor ? `&cursor=${encodeURIComponent(cursor)}` : ""}`,
      RuntimeTimelinePageSchema,
    );
  }
  node(nodeId: string, etag?: string) {
    return this.request<RuntimeNodeView | null>(
      "GET",
      `/v1/nodes/${RuntimeNodeIdSchema.parse(nodeId)}?schema_version=1`,
      RuntimeNodeViewSchema,
      undefined,
      etag ? { "If-None-Match": etag } : {},
    );
  }
  catalog() {
    return this.request("GET", "/v1/abilities", RuntimeAbilityCatalogSchema);
  }
  prepareInstance(input: RuntimeAbilityInstanceCreate) {
    return this.request(
      "POST",
      "/v1/ability-instances",
      RuntimeAbilityInstanceSchema,
      RuntimeAbilityInstanceCreateSchema.parse(input),
    );
  }
  abilities(agentId: string) {
    return this.request(
      "GET",
      `/v1/agents/${RuntimeAgentIdSchema.parse(agentId)}/abilities`,
      RuntimeAbilitiesViewSchema,
    );
  }
  grant(agentId: string, input: RuntimeGrantRequest, etag: string) {
    const request = RuntimeGrantRequestSchema.parse(input);
    return this.request(
      "PUT",
      `/v1/agents/${RuntimeAgentIdSchema.parse(agentId)}/abilities/grants/${encodeURIComponent(request.alias)}`,
      RuntimeAbilitiesViewSchema,
      request,
      { "If-Match": etag },
    );
  }
  revoke(agentId: string, alias: string, etag: string) {
    return this.request(
      "DELETE",
      `/v1/agents/${RuntimeAgentIdSchema.parse(agentId)}/abilities/grants/${encodeURIComponent(alias)}`,
      RuntimeAbilitiesViewSchema,
      undefined,
      { "If-Match": etag },
    );
  }
  bindTool(agentId: string, input: RuntimeToolRequest, etag: string) {
    const request = RuntimeToolRequestSchema.parse(input);
    return this.request(
      "PUT",
      `/v1/agents/${RuntimeAgentIdSchema.parse(agentId)}/abilities/tools/${encodeURIComponent(request.tool_name)}`,
      RuntimeAbilitiesViewSchema,
      request,
      { "If-Match": etag },
    );
  }
  unbindTool(agentId: string, name: string, etag: string) {
    return this.request(
      "DELETE",
      `/v1/agents/${RuntimeAgentIdSchema.parse(agentId)}/abilities/tools/${encodeURIComponent(name)}`,
      RuntimeAbilitiesViewSchema,
      undefined,
      { "If-Match": etag },
    );
  }
  changes(agentId: string, after = 0, limit = 100) {
    return this.request(
      "GET",
      `/v1/agents/${RuntimeAgentIdSchema.parse(agentId)}/abilities/changes?after_seq=${after}&limit=${limit}`,
      RuntimeAbilityChangesSchema,
    );
  }
}
