/** Agent 模板 REST 客户端：路径、查询参数与视图校验。 */

import { describe, expect, it, vi } from "vitest";

import { AgentTemplatesApi } from "./agent-templates.js";
import type { HttpClient } from "./http.js";

const DETAIL = {
  format: "ghrah.agent-templates.v1",
  template_id: "t1",
  revision: 1,
  is_latest: true,
  metadata: { namespace: "writing", name: "weekly" },
  model: { endpoint_name: "default-llm" },
  prompt: { system_prompt: "" },
  context_policy: { max_iterations: -1, communication_timeout: 300 },
  available_members: [],
  permission_request: {},
  path_request: [],
  reference_count: 0,
  created_at: "2026-11-18T00:00:00Z",
  updated_at: "2026-11-18T00:00:00Z",
  archived_at: null,
};

function fakeHttp() {
  const calls: { method: string; path: string; query?: unknown; body?: unknown }[] = [];
  const http = {
    get: vi.fn(async (path: string, options?: { query?: unknown }) => {
      calls.push({ method: "GET", path, query: options?.query });
      if (path.endsWith("/revisions")) {
        return { template_id: "t1", revisions: [] };
      }
      if (path.endsWith("/references")) {
        return { template_id: "t1", agents: [] };
      }
      if (path === "/api/v1/agent-templates") {
        return { templates: [] };
      }
      return DETAIL;
    }),
    post: vi.fn(async (path: string, body?: unknown) => {
      calls.push({ method: "POST", path, body });
      return DETAIL;
    }),
    put: vi.fn(async (path: string, body?: unknown) => {
      calls.push({ method: "PUT", path, body });
      return DETAIL;
    }),
  };
  return { http: http as unknown as HttpClient, calls };
}

describe("AgentTemplatesApi", () => {
  it("lists without query noise and omits default flags", async () => {
    const { http, calls } = fakeHttp();
    await new AgentTemplatesApi(http).list();
    expect(calls[0]).toEqual({
      method: "GET",
      path: "/api/v1/agent-templates",
      query: {
        namespace: undefined,
        name: undefined,
        tag: undefined,
        deprecated: undefined,
        include_archived: undefined,
        include_history: undefined,
      },
    });
  });

  it("maps camelCase options to snake_case query keys", async () => {
    const { http, calls } = fakeHttp();
    await new AgentTemplatesApi(http).list({ includeHistory: true, namespace: "writing" });
    expect(calls[0]?.query).toMatchObject({
      include_history: true,
      namespace: "writing",
    });
  });

  it("encodes template id in every item path", async () => {
    const { http, calls } = fakeHttp();
    const api = new AgentTemplatesApi(http);
    await api.get("a/b");
    await api.versions("a/b");
    await api.references("a/b");
    expect(calls.map((call) => call.path)).toEqual([
      "/api/v1/agent-templates/a%2Fb",
      "/api/v1/agent-templates/a%2Fb/revisions",
      "/api/v1/agent-templates/a%2Fb/references",
    ]);
  });

  it("creates, updates and archives with the protocol bodies", async () => {
    const { http, calls } = fakeHttp();
    const api = new AgentTemplatesApi(http);
    await api.create({
      metadata: { namespace: "writing", name: "weekly" },
    } as unknown as Parameters<AgentTemplatesApi["create"]>[0]);
    await api.update("t1", {
      expected_revision: 1,
      title: "周报",
    } as unknown as Parameters<AgentTemplatesApi["update"]>[1]);
    await api.archive("t1", 3);
    expect(calls.map((call) => [call.method, call.path])).toEqual([
      ["POST", "/api/v1/agent-templates"],
      ["PUT", "/api/v1/agent-templates/t1"],
      ["POST", "/api/v1/agent-templates/t1/archive"],
    ]);
    expect(calls[2]?.body).toEqual({ expected_revision: 3 });
  });
});
