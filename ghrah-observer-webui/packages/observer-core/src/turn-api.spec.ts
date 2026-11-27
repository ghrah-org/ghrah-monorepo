// SPDX-FileCopyrightText: 2026 chenxya <chenxya@ghrah.org>
//
// SPDX-License-Identifier: Apache-2.0

import { expect, it, vi } from "vitest";
import { createHttpAbortController, HttpClient, type HttpFetch } from "./http.js";
import { TurnApiClient } from "./turn-api.js";

function response(body: unknown, status = 200) {
  return {
    ok: status === 200,
    status,
    headers: { get: () => "application/json" },
    json: async () => body,
    text: async () => JSON.stringify(body),
  };
}

it("encodes Room and Agent paths and preserves a zero cursor", async () => {
  const fetch = vi
    .fn<HttpFetch>()
    .mockResolvedValueOnce(
      response({
        room_id: "r+1",
        project_id: "p/x",
        entries: [],
        turns: [],
        next_before_seq: null,
        next_since_seq: 0,
        latest_seq: 0,
        has_more: false,
        read_at: "2026-11-26T00:00:00Z",
      }),
    )
    .mockResolvedValueOnce(response({ agent_id: "agent+1", items: [] }))
    .mockResolvedValueOnce(response({ agent_id: "agent+1", items: [] }))
    .mockResolvedValueOnce(
      response({ error: { code: "not_found", message: "missing turn" } }, 404),
    );
  const api = new TurnApiClient(new HttpClient({ baseUrl: "http://localhost", fetchImpl: fetch }));

  await expect(api.roomTranscript("p/x", "r+1", { sinceSeq: 0, limit: 10 })).resolves.toMatchObject(
    { next_since_seq: 0 },
  );
  await api.listTurns("p/x", "agent+1", { status: "open", sessionId: "s/1", limit: 5 });
  await api.hitlPending("p/x", "agent+1", { sessionId: "s/1" });
  await expect(api.turnDetail("p/x", "agent+1", "t/1")).rejects.toMatchObject({
    status: 404,
    code: "not_found",
  });
  expect(fetch.mock.calls.map(([url]) => url)).toEqual([
    "http://localhost/api/v1/projects/p%2Fx/rooms/r%2B1/transcript?since_seq=0&limit=10",
    "http://localhost/api/v1/projects/p%2Fx/agents/agent%2B1/turns?session_id=s%2F1&status=open&limit=5",
    "http://localhost/api/v1/projects/p%2Fx/agents/agent%2B1/hitl/pending?session_id=s%2F1",
    "http://localhost/api/v1/projects/p%2Fx/agents/agent%2B1/turns/t%2F1",
  ]);
});

it("rejects conflicting cursors locally and malformed responses at the boundary", async () => {
  const fetch = vi.fn<HttpFetch>().mockResolvedValue(response({ entries: [] }));
  const api = new TurnApiClient(new HttpClient({ baseUrl: "http://localhost", fetchImpl: fetch }));
  await expect(api.roomTranscript("p", "r", { sinceSeq: 0, beforeSeq: 1 })).rejects.toMatchObject({
    code: "invalid_request",
    status: 0,
  });
  expect(fetch).not.toHaveBeenCalled();
  await expect(api.roomTranscript("p", "r")).rejects.toMatchObject({
    code: "internal",
    detail: expect.any(Array),
  });
});

it("propagates cancellation and the service's archive error", async () => {
  const controller = createHttpAbortController();
  const fetch = vi
    .fn<HttpFetch>()
    .mockResolvedValue(
      response({ error: { code: "resource_archived", message: "archived" } }, 409),
    );
  const api = new TurnApiClient(new HttpClient({ baseUrl: "http://localhost", fetchImpl: fetch }));
  controller.abort();
  await expect(api.roomTranscript("p", "r", { signal: controller.signal })).rejects.toMatchObject({
    code: "resource_archived",
    status: 409,
  });
  expect(fetch.mock.calls[0][1].signal.aborted).toBe(true);
});
