// SPDX-FileCopyrightText: 2026 chenxya <chenxya@ghrah.org>
//
// SPDX-License-Identifier: Apache-2.0

import { AgentAdminViewSchema, AgentDeletedViewSchema } from "@ghrah/protocol";
import { describe, expect, it, vi } from "vitest";
import { createAgentControlClient } from "./agent-control.js";
import { HttpClient, HttpError, type HttpFetch, type HttpFetchResponse } from "./http.js";

const PROJECT_ID = "p1";
const AGENT_ID = "a1";

function jsonResponse(body: unknown, status = 200): HttpFetchResponse {
  return {
    ok: status >= 200 && status < 300,
    status,
    headers: {
      get: (name: string) =>
        name.toLowerCase() === "content-type" ? "application/json; charset=utf-8" : null,
    },
    json: async () => JSON.parse(JSON.stringify(body)),
    text: async () => JSON.stringify(body),
  };
}

function view(overrides: Record<string, unknown> = {}) {
  return {
    project_id: PROJECT_ID,
    specification: {
      agent_id: AGENT_ID,
      name: "Coder",
      presentation: {},
      desired_state: "running",
      runtime_status: "running",
      project_version: 5,
    },
    runtime: { availability: "online", incarnation_id: "i1", busy: false },
    ...overrides,
  };
}

function clientWith(fetchImpl: HttpFetch) {
  return createAgentControlClient(new HttpClient({ baseUrl: "http://localhost:4112", fetchImpl }), {
    projectId: PROJECT_ID,
  });
}

describe("AgentControlClient", () => {
  it("reads one agent resource from the project path", async () => {
    const fetchImpl = vi.fn<HttpFetch>().mockResolvedValue(jsonResponse(view()));
    const client = clientWith(fetchImpl);

    const admin = await client.get(AGENT_ID);

    expect(admin.runtime.busy).toBe(false);
    const [url, init] = fetchImpl.mock.calls[0] ?? [];
    expect(String(url)).toContain(`/api/v1/projects/${PROJECT_ID}/agents/${AGENT_ID}`);
    expect(init?.method).toBe("GET");
  });

  it("rejects a view for another target", async () => {
    const fetchImpl = vi
      .fn<HttpFetch>()
      .mockResolvedValue(
        jsonResponse(view({ specification: { ...view().specification, agent_id: "b2" } })),
      );
    const client = clientWith(fetchImpl);

    await expect(client.get(AGENT_ID)).rejects.toThrow("Agent control target mismatch");
  });

  it("declares the desired state with the project version as CAS token", async () => {
    const fetchImpl = vi.fn<HttpFetch>().mockResolvedValue(jsonResponse(view()));
    const client = clientWith(fetchImpl);

    await client.setDesiredState(AGENT_ID, "stopped", 5);

    const [url, init] = fetchImpl.mock.calls[0] ?? [];
    expect(String(url)).toContain(
      `/api/v1/projects/${PROJECT_ID}/agents/${AGENT_ID}/desired-state`,
    );
    expect(init?.method).toBe("PUT");
    expect(JSON.parse(String(init?.body))).toEqual({ expected_revision: 5, state: "stopped" });
  });

  it("deletes with expected_revision as a query parameter", async () => {
    const deleted = AgentDeletedViewSchema.parse({
      project_id: PROJECT_ID,
      agent_id: AGENT_ID,
      project_version: 6,
    });
    const fetchImpl = vi.fn<HttpFetch>().mockResolvedValue(jsonResponse(deleted));
    const client = clientWith(fetchImpl);

    const result = await client.delete(AGENT_ID, 5);

    expect(result.project_version).toBe(6);
    const [url, init] = fetchImpl.mock.calls[0] ?? [];
    expect(String(url)).toContain(`/api/v1/projects/${PROJECT_ID}/agents/${AGENT_ID}`);
    expect(String(url)).toContain("expected_revision=5");
    expect(init?.method).toBe("DELETE");
  });

  it("surfaces the busy rejection as an HttpError with its code", async () => {
    const fetchImpl = vi.fn<HttpFetch>().mockResolvedValue(
      jsonResponse(
        {
          error: {
            code: "agent_busy",
            message: "agent Coder is processing messages",
            detail: null,
          },
        },
        409,
      ),
    );
    const client = clientWith(fetchImpl);

    const error = await client.delete(AGENT_ID, 5).catch((caught: unknown) => caught);

    expect(error).toBeInstanceOf(HttpError);
    expect((error as HttpError).code).toBe("agent_busy");
    expect((error as HttpError).status).toBe(409);
  });

  it("parses a successful update as an admin view", async () => {
    const fetchImpl = vi
      .fn<HttpFetch>()
      .mockResolvedValue(
        jsonResponse(
          view({ specification: { ...view().specification, desired_state: "stopped" } }),
        ),
      );

    const admin = await clientWith(fetchImpl).setDesiredState(AGENT_ID, "stopped", 5);

    expect(AgentAdminViewSchema.parse(admin).specification.desired_state).toBe("stopped");
  });
});
