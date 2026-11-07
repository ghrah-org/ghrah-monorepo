import { describe, expect, it, vi } from "vitest";

import {
  deriveHttpBase,
  HttpClient,
  HttpError,
  type HttpFetch,
  type HttpFetchResponse,
} from "./http.js";

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

describe("deriveHttpBase", () => {
  it("derives http(s) origin from ws(s) urls", () => {
    expect(deriveHttpBase("ws://localhost:4112/ws")).toBe("http://localhost:4112");
    expect(deriveHttpBase("wss://example.test/ws")).toBe("https://example.test");
  });
});

describe("HttpClient", () => {
  it("adds bearer token, serializes arrays in query and parses json", async () => {
    const fetchImpl = vi.fn<HttpFetch>().mockResolvedValue(jsonResponse({ ok: true }));
    const client = new HttpClient({
      baseUrl: "http://localhost:4112/",
      token: "secret",
      fetchImpl,
    });
    await expect(
      client.get("/api/v1/x", { query: { a: 1, skip: undefined, list: ["x", "y"] } }),
    ).resolves.toEqual({ ok: true });
    const [url, init] = fetchImpl.mock.calls[0] as [string, { headers: Record<string, string> }];
    expect(url).toContain("?a=1&list=x&list=y");
    expect(init.headers.Authorization).toBe("Bearer secret");
  });

  it("normalizes server error envelopes and HTTP status fallbacks", async () => {
    const fetchImpl = vi
      .fn<HttpFetch>()
      .mockResolvedValueOnce(
        jsonResponse(
          { error: { code: "conflict", message: "revision changed", detail: { revision: 4 } } },
          409,
        ),
      )
      .mockResolvedValueOnce(jsonResponse({}, 503));
    const client = new HttpClient({ baseUrl: "http://localhost", fetchImpl });
    const conflict = await client.get("/api/v1/x").catch((error: unknown) => error);
    expect(conflict).toBeInstanceOf(HttpError);
    expect((conflict as HttpError).code).toBe("conflict");
    expect((conflict as HttpError).detail).toEqual({ revision: 4 });
    const unavailable = await client.get("/api/v1/y").catch((error: unknown) => error);
    expect((unavailable as HttpError).code).toBe("unavailable");
  });

  it("retries a GET once on network failure but never retries writes", async () => {
    const fetchImpl = vi
      .fn<HttpFetch>()
      .mockRejectedValueOnce(new TypeError("network down"))
      .mockResolvedValueOnce(jsonResponse({ ok: true }));
    const client = new HttpClient({ baseUrl: "http://localhost", fetchImpl });
    await expect(client.get("/api/v1/x")).resolves.toEqual({ ok: true });
    expect(fetchImpl).toHaveBeenCalledTimes(2);

    const reject = vi.fn<HttpFetch>().mockRejectedValue(new TypeError("network down"));
    const writeClient = new HttpClient({ baseUrl: "http://localhost", fetchImpl: reject });
    await expect(writeClient.put("/api/v1/x", {})).rejects.toBeInstanceOf(HttpError);
    expect(reject).toHaveBeenCalledTimes(1);
  });

  it("reports a timeout as an unavailable HttpError", async () => {
    const fetchImpl = vi.fn<HttpFetch>(
      (_url, init) =>
        new Promise<HttpFetchResponse>((_resolve, reject) => {
          init.signal.addEventListener("abort", () => {
            reject(Object.assign(new Error("aborted"), { name: "AbortError" }));
          });
        }),
    );
    const client = new HttpClient({ baseUrl: "http://localhost", fetchImpl });
    const error = await client.get("/api/v1/x", { timeoutMs: 5 }).catch((e: unknown) => e);
    expect(error).toBeInstanceOf(HttpError);
    expect((error as HttpError).status).toBe(0);
    expect((error as HttpError).message).toContain("超时");
  });
});
