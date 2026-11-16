// SPDX-FileCopyrightText: 2026 chenxya <chenxya@ghrah.org>
//
// SPDX-License-Identifier: Apache-2.0

import { afterEach, describe, expect, it, vi } from "vitest";
import {
  artifactContentUrl,
  createArtifactsClient,
  setArtifactsClientForTest,
  type ArtifactsFetch,
  type ArtifactsFetchResponse,
} from "./artifacts.js";
import { HttpError } from "./http.js";

function bytesOf(text: string): ArrayBuffer {
  const bytes = new Uint8Array(text.length);
  for (let index = 0; index < text.length; index += 1) bytes[index] = text.charCodeAt(index);
  return bytes.buffer;
}

function textOf(bytes: Uint8Array): string {
  let out = "";
  for (const byte of bytes) out += String.fromCharCode(byte);
  return out;
}

function response(
  body: unknown,
  init: { status?: number; contentType?: string } = {},
): ArtifactsFetchResponse {
  const status = init.status ?? 200;
  const text = typeof body === "string" ? body : JSON.stringify(body);
  return {
    ok: status >= 200 && status < 300,
    status,
    headers: {
      get: (name: string) =>
        name.toLowerCase() === "content-type" ? (init.contentType ?? "application/json") : null,
    },
    json: async () => JSON.parse(text),
    text: async () => text,
    arrayBuffer: async () => bytesOf(text),
  };
}

type RecordedInit = {
  method: string;
  headers: Record<string, string>;
  body?: string;
  signal?: { aborted: boolean; addEventListener(type: "abort", listener: () => void): void };
};

function clientWith(
  handler: (
    url: string,
    init: RecordedInit,
  ) => ArtifactsFetchResponse | Promise<ArtifactsFetchResponse>,
  token?: string,
) {
  const calls: Array<{ url: string; init: RecordedInit }> = [];
  const fetchImpl: ArtifactsFetch = async (url, init) => {
    const recorded: RecordedInit = {
      method: init.method,
      headers: init.headers,
      ...(init.body !== undefined ? { body: init.body } : {}),
      ...(init.signal ? { signal: init.signal } : {}),
    };
    calls.push({ url, init: recorded });
    return handler(url, recorded);
  };
  const client = createArtifactsClient({
    baseUrl: "http://localhost:4112",
    token: token ?? "secret",
    fetchImpl,
  });
  return { client, calls };
}

const artifact = {
  artifact_id: "a".repeat(64),
  filename: "REQ-DRAFT-0001-requirement.svg",
  mime_type: "image/svg+xml",
  size: 1234,
  title: "Requirement",
  published_at: "2026-11-12T00:00:00Z",
  source: { workspace_id: "w1", path: "outputs/requirements/REQ.svg", agent_id: "a1" },
};

afterEach(() => setArtifactsClientForTest(null));

describe("listWorkspaceFiles", () => {
  it("GETs the encoded directory path and parses entries", async () => {
    const { client, calls } = clientWith(() =>
      response({
        path: "outputs/requirements",
        entries: [
          { name: "REQ.svg", path: "outputs/requirements/REQ.svg", type: "file", size: 12 },
          { name: "nested", path: "outputs/requirements/nested", type: "dir" },
          { name: "bad" },
        ],
      }),
    );
    const listing = await client.listWorkspaceFiles("p1", "w1", "outputs/requirements");
    expect(calls[0].url).toBe(
      "http://localhost:4112/api/v1/projects/p1/workspaces/w1/files?path=outputs%2Frequirements",
    );
    expect(calls[0].init.method).toBe("GET");
    expect(calls[0].init.headers.Authorization).toBe("Bearer secret");
    expect(listing.path).toBe("outputs/requirements");
    expect(listing.entries).toHaveLength(2);
    expect(listing.entries[0]).toMatchObject({ name: "REQ.svg", type: "file", size: 12 });
    expect(listing.entries[1]).toMatchObject({ name: "nested", type: "dir" });
  });

  it("defaults the path to the workspace root", async () => {
    const { client, calls } = clientWith(() => response({ path: "", entries: [] }));
    await client.listWorkspaceFiles("p1", "w1");
    expect(calls[0].url).toContain("/files?path=");
  });
});

describe("URL builders", () => {
  it("encodes workspace file paths and artifact content paths", () => {
    const { client } = clientWith(() => response({}));
    expect(client.workspaceFileContentUrl("p1", "w1", "outputs/九夏 a.svg")).toBe(
      "http://localhost:4112/api/v1/projects/p1/workspaces/w1/file?path=outputs%2F%E4%B9%9D%E5%A4%8F%20a.svg",
    );
    expect(client.workspaceFileUrl("p1", "w1", "outputs/a.dxf")).toBe(
      "http://localhost:4112/api/v1/projects/p1/workspaces/w1/file?path=outputs%2Fa.dxf",
    );
    expect(client.artifactContentUrl("p1", "a")).toBe(
      "http://localhost:4112/api/v1/projects/p1/artifacts/a/content",
    );
    expect(() => client.artifactContentUrl("p1", "bad id")).toThrow(HttpError);
  });

  it("shared client delegates after test injection", () => {
    const { client } = clientWith(() => response({}));
    setArtifactsClientForTest(client);
    expect(artifactContentUrl("p1", "a")).toContain("/api/v1/projects/p1/artifacts/a/content");
  });
});

describe("publishArtifact / listArtifacts", () => {
  it("publishes with snake_case body and parses the artifact view", async () => {
    const { client, calls } = clientWith(() => response(artifact, { status: 201 }));
    const view = await client.publishArtifact("p1", {
      workspace_id: "w1",
      path: "outputs/requirements/REQ.svg",
      mime_type: "image/svg+xml",
      title: "Requirement",
    });
    expect(calls[0].init.method).toBe("POST");
    expect(calls[0].url).toBe("http://localhost:4112/api/v1/projects/p1/artifacts");
    expect(calls[0].init.headers["Content-Type"]).toBe("application/json");
    expect(JSON.parse(String(calls[0].init.body))).toEqual({
      workspace_id: "w1",
      path: "outputs/requirements/REQ.svg",
      mime_type: "image/svg+xml",
      title: "Requirement",
    });
    expect(view).toMatchObject({
      artifact_id: artifact.artifact_id,
      filename: artifact.filename,
      source: { workspace_id: "w1", path: "outputs/requirements/REQ.svg" },
    });
  });

  it("lists artifacts and tolerates malformed rows", async () => {
    const { client } = clientWith(() =>
      response({ artifacts: [artifact, { artifact_id: "broken" }] }),
    );
    const list = await client.listArtifacts("p1");
    expect(list).toHaveLength(1);
    expect(list[0].title).toBe("Requirement");
  });
});

describe("errors and bytes", () => {
  it("maps server error codes and status fallbacks", async () => {
    const missing = clientWith(() =>
      response({ error: { code: "workspace_not_found", message: "no ws" } }, { status: 404 }),
    );
    const first = await missing.client.listWorkspaceFiles("p1", "w1").catch((error) => error);
    expect(first).toBeInstanceOf(HttpError);
    expect((first as HttpError).code).toBe("workspace_not_found");

    const tooLarge = clientWith(() => response("oops", { status: 413 }));
    const second = await tooLarge.client.listArtifacts("p1").catch((error) => error);
    expect((second as HttpError).code).toBe("artifact_too_large");
  });

  it("normalizes network failures", async () => {
    const { client } = clientWith(() => {
      throw new Error("offline");
    });
    const error = await client.listArtifacts("p1").catch((caught) => caught);
    expect((error as HttpError).code).toBe("unavailable");
  });

  it("times out slow requests", async () => {
    vi.useFakeTimers();
    try {
      const { client } = clientWith(
        (_url, init) =>
          new Promise<ArtifactsFetchResponse>((_resolve, reject) => {
            // 模拟真实 fetch：只有收到 abort 才拒绝。
            init.signal?.addEventListener("abort", () => reject(new Error("aborted")));
          }),
      );
      const pending = client.listArtifacts("p1").catch((error) => error);
      await vi.advanceTimersByTimeAsync(30_000);
      const error = (await pending) as HttpError;
      expect(error.code).toBe("unavailable");
      expect(error.message).toContain("超时");
    } finally {
      vi.useRealTimers();
    }
  });

  it("reads bytes with auth and content type", async () => {
    const { client, calls } = clientWith(() =>
      response("<svg/>", { contentType: "image/svg+xml" }),
    );
    const file = await client.readWorkspaceFile("p1", "w1", "outputs/a.svg");
    expect(calls[0].url).toContain("/workspaces/w1/file?path=outputs%2Fa.svg");
    expect(calls[0].init.headers.Authorization).toBe("Bearer secret");
    expect(file.mimeType).toBe("image/svg+xml");
    expect(textOf(file.bytes)).toBe("<svg/>");

    const artifactBytes = await client.readArtifact("p1", "a");
    expect(calls[1].url).toContain("/artifacts/a/content");
    expect(artifactBytes.mimeType).toBe("image/svg+xml");
  });
});
