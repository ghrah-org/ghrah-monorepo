// SPDX-FileCopyrightText: 2026 chenxya <chenxya@ghrah.org>
//
// SPDX-License-Identifier: Apache-2.0

import { describe, expect, it } from "vitest";
import {
  attachmentPath,
  attachmentUrl,
  createAttachmentClient,
  DEFAULT_ATTACHMENT_TIMEOUT_MS,
  setAttachmentClientForTest,
  type UploadBody,
  type UploadXhr,
  type UploadXhrProgressEvent,
} from "./attachments.js";
import { HttpError } from "./http.js";

class FakeXhr implements UploadXhr {
  upload = {
    progress: null as ((event: UploadXhrProgressEvent) => void) | null,
    addEventListener: (type: "progress", listener: (event: UploadXhrProgressEvent) => void) => {
      if (type === "progress") this.upload.progress = listener;
    },
  };
  method = "";
  url = "";
  headers: Record<string, string> = {};
  sent: UploadBody | null = null;
  timeout = 0;
  status = 200;
  responseText = "";
  private listeners: Record<string, (() => void) | undefined> = {};

  open(method: string, url: string) {
    this.method = method;
    this.url = url;
  }
  setRequestHeader(name: string, value: string) {
    this.headers[name] = value;
  }
  send(body: UploadBody) {
    this.sent = body;
  }
  abort() {
    this.listeners.abort?.();
  }
  addEventListener(type: "load" | "error" | "abort" | "timeout", listener: () => void) {
    this.listeners[type] = listener;
  }
  emitProgress(loaded: number, total: number) {
    this.upload.progress?.({ loaded, total });
  }
  load() {
    this.listeners.load?.();
  }
  fail() {
    this.listeners.error?.();
  }
  timeOut() {
    this.listeners.timeout?.();
  }
}

function clientWith(overrides: { token?: string } = {}) {
  const fake = new FakeXhr();
  const client = createAttachmentClient({
    baseUrl: "http://localhost:4112",
    token: overrides.token ?? "secret",
    xhrFactory: () => fake,
  });
  return { fake, client };
}

describe("attachmentPath / attachmentUrl", () => {
  it("builds encoded project and attachment paths", () => {
    expect(attachmentPath("p1")).toBe("/api/v1/projects/p1/attachments");
    expect(attachmentPath("p1", "abc")).toBe("/api/v1/projects/p1/attachments/abc");
    expect(() => attachmentPath("bad/id")).toThrow(HttpError);
    expect(() => attachmentPath("p1", "bad id")).toThrow(HttpError);
  });

  it("attachmentUrl uses the configured base and resets to shared client", () => {
    const { client } = clientWith();
    expect(client.url("p1", "a1")).toBe("http://localhost:4112/api/v1/projects/p1/attachments/a1");
    setAttachmentClientForTest(client);
    expect(attachmentUrl("p1", "a1")).toBe(
      "http://localhost:4112/api/v1/projects/p1/attachments/a1",
    );
    setAttachmentClientForTest(null);
  });
});

describe("uploadAttachment", () => {
  it("POSTs raw bytes with MIME, encoded filename, auth, and reports progress", async () => {
    const { fake, client } = clientWith();
    const progress: Array<{ loaded: number; total: number }> = [];
    const bytes = new Uint8Array([1, 2, 3]);
    const pending = client.upload("p1", bytes, {
      mimeType: "video/mp4",
      filename: "演示 视频.mp4",
      onProgress: (event) => progress.push(event),
    });
    expect(fake.method).toBe("POST");
    expect(fake.url).toBe("http://localhost:4112/api/v1/projects/p1/attachments");
    expect(fake.headers["Content-Type"]).toBe("video/mp4");
    expect(fake.headers["X-Filename"]).toBe(encodeURIComponent("演示 视频.mp4"));
    expect(fake.headers.Authorization).toBe("Bearer secret");
    expect(fake.timeout).toBe(DEFAULT_ATTACHMENT_TIMEOUT_MS);
    expect(fake.sent).toBe(bytes);

    fake.emitProgress(3, 10);
    fake.responseText = JSON.stringify({
      attachment_id: "abc123",
      size: 10,
      mime_type: "video/mp4",
      filename: "演示 视频.mp4",
    });
    fake.load();

    await expect(pending).resolves.toEqual({
      attachmentId: "abc123",
      size: 10,
      mimeType: "video/mp4",
      filename: "演示 视频.mp4",
    });
    expect(progress).toEqual([{ loaded: 3, total: 10 }]);
  });

  it("omits auth/filename headers when absent", async () => {
    const { fake, client } = clientWith({ token: "" });
    const pending = client.upload("p1", new ArrayBuffer(2), { mimeType: "video/webm" });
    expect(fake.headers.Authorization).toBeUndefined();
    expect(fake.headers["X-Filename"]).toBeUndefined();
    fake.responseText = JSON.stringify({ attachment_id: "id", size: 2, mime_type: "video/webm" });
    fake.load();
    await expect(pending).resolves.toMatchObject({ attachmentId: "id", filename: null });
  });

  it("maps HTTP error bodies and status codes to HttpError", async () => {
    const { fake, client } = clientWith();
    const tooLarge = client.upload("p1", new Uint8Array(1), { mimeType: "video/mp4" });
    fake.status = 413;
    fake.responseText = JSON.stringify({
      error: { code: "attachment_too_large", message: "too big", detail: { max: 10 } },
    });
    fake.load();
    const first = await tooLarge.catch((error: unknown) => error);
    expect(first).toBeInstanceOf(HttpError);
    expect((first as HttpError).code).toBe("attachment_too_large");
    expect((first as HttpError).status).toBe(413);

    const second = client.upload("p1", new Uint8Array(1), { mimeType: "video/mp4" });
    fake.status = 415;
    fake.responseText = "not-json";
    fake.load();
    const secondError = await second.catch((error: unknown) => error);
    expect((secondError as HttpError).code).toBe("unsupported_attachment_type");
  });

  it("normalizes network, timeout, and abort failures", async () => {
    const { fake, client } = clientWith();
    const network = client.upload("p1", new Uint8Array(1), { mimeType: "video/mp4" });
    fake.fail();
    expect(((await network.catch((error: unknown) => error)) as HttpError).code).toBe(
      "unavailable",
    );

    const timed = client.upload("p1", new Uint8Array(1), { mimeType: "video/mp4" });
    fake.timeOut();
    expect(((await timed.catch((error: unknown) => error)) as HttpError).code).toBe("timeout");

    const controller = {
      aborted: false,
      listeners: [] as Array<() => void>,
      addEventListener(_type: "abort", listener: () => void) {
        this.listeners.push(listener);
      },
    };
    const aborted = client.upload("p1", new Uint8Array(1), {
      mimeType: "video/mp4",
      signal: controller,
    });
    controller.aborted = true;
    controller.listeners[0]?.();
    expect(((await aborted.catch((error: unknown) => error)) as HttpError).code).toBe("aborted");
  });

  it("rejects a malformed success body", async () => {
    const { fake, client } = clientWith();
    const pending = client.upload("p1", new Uint8Array(1), { mimeType: "video/mp4" });
    fake.responseText = JSON.stringify({ size: 1 });
    fake.load();
    const error = (await pending.catch((caught: unknown) => caught)) as HttpError;
    expect(error.code).toBe("internal");
    expect(error.message).toContain("attachment_id");
  });
});
