import { describe, expect, it, vi } from "vitest";
import fixture from "../../protocol/src/__fixtures__/ability-tree.json";
import { AbilityConflictError, createAbilityControlClient } from "./ability-control.js";
import { HttpClient, type HttpFetch, type HttpFetchResponse } from "./http.js";

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

function clientWith(fetchImpl: HttpFetch) {
  return createAbilityControlClient(
    new HttpClient({ baseUrl: "http://localhost:4112", fetchImpl }),
    { projectId: fixture.initial.project_id },
  );
}

describe("AbilityControlClient", () => {
  it("reads the tree and rejects responses for another target", async () => {
    const fetchImpl = vi.fn<HttpFetch>().mockResolvedValue(jsonResponse(fixture.initial));
    const client = clientWith(fetchImpl);
    await expect(client.tree(fixture.initial.agent_id)).resolves.toEqual(fixture.initial);
    expect(fetchImpl.mock.calls[0]?.[0]).toContain(
      `/api/v1/projects/${fixture.initial.project_id}/agents/${fixture.initial.agent_id}/ability-tree`,
    );
    await expect(client.tree("bob")).rejects.toThrow("target mismatch");
  });

  it("maps 409 to an authoritative conflict error without retrying", async () => {
    const view = { ...fixture.initial, revision: fixture.initial.revision + 1 };
    const fetchImpl = vi.fn<HttpFetch>().mockResolvedValue(
      jsonResponse(
        {
          error: {
            code: "conflict",
            message: "relation revision changed",
            detail: { revision: view.revision, view },
          },
        },
        409,
      ),
    );
    const client = clientWith(fetchImpl);
    const error = await client
      .putGrant(fixture.initial.agent_id, {
        alias: "files",
        instance_id: "files-1",
        members: [],
        allowed_roots: [],
        skills: [],
        skill: null,
        whole_container: true,
        axes: [],
        policy: null,
        expected_revision: fixture.initial.revision,
      })
      .catch((caught: unknown) => caught);
    expect(error).toBeInstanceOf(AbilityConflictError);
    expect((error as AbilityConflictError).revision).toBe(view.revision);
    expect((error as AbilityConflictError).view?.revision).toBe(view.revision);
    expect(fetchImpl).toHaveBeenCalledTimes(1);
  });

  it("sends revoke members through the query and rejects invalid content members", async () => {
    const change = {
      format: "ghrah.ability-control.change.v1",
      view: { ...fixture.initial, revision: fixture.initial.revision + 1 },
      revision: fixture.initial.revision + 1,
    };
    const fetchImpl = vi.fn<HttpFetch>().mockResolvedValue(jsonResponse(change));
    const client = clientWith(fetchImpl);
    await client.revokeGrant(fixture.initial.agent_id, {
      alias: "files",
      expected_revision: fixture.initial.revision,
      members: ["operations/read_file"],
    });
    const [url, init] = fetchImpl.mock.calls[0] as [string, { method: string }];
    expect(init.method).toBe("DELETE");
    expect(url).toContain("expected_revision=");
    expect(url).toContain("members=operations%2Fread_file");
    await expect(
      client.content(fixture.initial.agent_id, "files", "skill/guide/../secret"),
    ).rejects.toThrow("Invalid content member");
  });
});
