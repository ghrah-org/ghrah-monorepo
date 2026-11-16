// SPDX-FileCopyrightText: 2026 chenxya <chenxya@ghrah.org>
//
// SPDX-License-Identifier: Apache-2.0

// @vitest-environment happy-dom

import type { UploadedAttachment } from "@ghrah/observer-core";
import { describe, expect, it, vi } from "vitest";
import {
  AttachmentError,
  base64FromDataUrl,
  isSupportedImageType,
  MAX_FILE_BASE64_CHARS,
  MAX_FILE_BYTES,
  MAX_IMAGE_BASE64_CHARS,
  MAX_TOTAL_BASE64_CHARS,
  MAX_VIDEO_BYTES,
  MAX_VIDEO_FRAMES,
  mimeFromDataUrl,
  useAttachments,
  type VideoMetadata,
} from "./useAttachments";

function pngFile(name = "a.png", type = "image/png"): File {
  return new File([Uint8Array.from([1, 2, 3])], name, { type });
}

function fileOf(name: string, type: string): File {
  return new File([Uint8Array.from([1, 2, 3])], name, { type });
}

function withSize(file: File, size: number): File {
  Object.defineProperty(file, "size", { configurable: true, value: size });
  return file;
}

function dataUrl(base64: string, mime = "image/png"): string {
  return `data:${mime};base64,${base64}`;
}

function videoMeta(frames = 2): VideoMetadata {
  return {
    durationS: 12.5,
    width: 1280,
    height: 720,
    frames: Array.from({ length: frames }, (_, index) => ({
      type: "image" as const,
      base64: `RlJBTUU${index}`,
      mime_type: "image/jpeg",
    })),
  };
}

function uploaded(overrides: Partial<UploadedAttachment> = {}): UploadedAttachment {
  return {
    attachmentId: "att-123",
    size: 100,
    mimeType: "video/mp4",
    filename: "demo.mp4",
    ...overrides,
  };
}

describe("attachment helpers", () => {
  it("splits base64 and mime from data URLs", () => {
    expect(base64FromDataUrl(dataUrl("aGk="))).toBe("aGk=");
    expect(base64FromDataUrl("not-a-data-url")).toBe("");
    expect(mimeFromDataUrl(dataUrl("aGk=", "image/webp"))).toBe("image/webp");
    expect(mimeFromDataUrl("not-a-data-url")).toBe("");
  });

  it("only accepts the image whitelist", () => {
    for (const mime of ["image/png", "image/jpeg", "image/webp", "image/gif"]) {
      expect(isSupportedImageType(mime)).toBe(true);
    }
    expect(isSupportedImageType("image/svg+xml")).toBe(false);
    expect(isSupportedImageType("text/plain")).toBe(false);
  });
});

describe("useAttachments images", () => {
  it("adds a compressed image and exposes a sendable image block", async () => {
    const state = useAttachments({
      deps: { compress: async () => dataUrl("aGk="), createId: () => "att-1" },
    });
    await state.addFiles([pngFile()]);
    expect(state.items.value).toEqual([
      {
        id: "att-1",
        kind: "image",
        name: "a.png",
        mime: "image/png",
        size: 3,
        base64: "aGk=",
        dataUrl: dataUrl("aGk="),
      },
    ]);
    expect(state.blocks.value).toEqual([{ type: "image", base64: "aGk=", mime_type: "image/png" }]);
    expect(state.hasAttachments.value).toBe(true);
    expect(state.hasReadyBlocks.value).toBe(true);
  });

  it("rejects an unlisted image subtype without adding an item", async () => {
    const state = useAttachments({ deps: { compress: async () => dataUrl("aGk=") } });
    await state.addFiles([fileOf("icon.svg", "image/svg+xml")]);
    expect(state.items.value).toHaveLength(0);
    expect(state.errors.value).toEqual(["chat.attachment.unsupported"]);
  });

  it("rejects a single block above the base64 limit", async () => {
    const oversized = dataUrl("A".repeat(MAX_IMAGE_BASE64_CHARS + 1));
    const state = useAttachments({ deps: { compress: async () => oversized } });
    await state.addFiles([pngFile()]);
    expect(state.items.value).toHaveLength(0);
    expect(state.errors.value).toEqual(["chat.attachment.tooLarge"]);
  });

  it("rejects when compression fails", async () => {
    const state = useAttachments({
      deps: {
        compress: async () => {
          throw new Error("boom");
        },
      },
    });
    await state.addFiles([pngFile()]);
    expect(state.items.value).toHaveLength(0);
    expect(state.errors.value).toEqual(["chat.attachment.failed"]);
  });

  it("caps images at 4 per message", async () => {
    const state = useAttachments({
      deps: {
        compress: async () => dataUrl("aGk="),
        createId: (() => {
          let index = 0;
          return () => `att-${++index}`;
        })(),
      },
    });
    await state.addFiles([pngFile("1.png"), pngFile("2.png"), pngFile("3.png"), pngFile("4.png")]);
    expect(state.items.value).toHaveLength(4);
    await state.addFiles([pngFile("5.png")]);
    expect(state.items.value).toHaveLength(4);
    expect(state.errors.value).toContain("chat.attachment.totalTooLarge");
  });

  it("enforces the total base64 budget across blocks", async () => {
    const chunk = "A".repeat(Math.floor(MAX_TOTAL_BASE64_CHARS / 2) - 4);
    const state = useAttachments({ deps: { compress: async () => dataUrl(chunk) } });
    await state.addFiles([pngFile("1.png"), pngFile("2.png")]);
    expect(state.items.value).toHaveLength(2);
    await state.addFiles([pngFile("3.png")]);
    expect(state.items.value).toHaveLength(2);
    expect(state.errors.value).toContain("chat.attachment.totalTooLarge");
  });

  it("remove/clear reset items and errors", async () => {
    const state = useAttachments({
      deps: { compress: async () => dataUrl("aGk="), createId: () => "att-1" },
    });
    await state.addFiles([pngFile(), fileOf("bad.svg", "image/svg+xml")]);
    expect(state.items.value).toHaveLength(1);
    state.remove("att-1");
    expect(state.items.value).toHaveLength(0);
    state.clear();
    expect(state.errors.value).toHaveLength(0);
  });
});

describe("useAttachments files", () => {
  it("inlines a small non-image file as a file block", async () => {
    const state = useAttachments({
      deps: {
        readAsDataUrl: async () => dataUrl("ZmlsZQ==", "text/plain"),
        createId: () => "att-f",
      },
    });
    await state.addFiles([fileOf("notes.md", "text/markdown")]);
    expect(state.items.value[0]).toMatchObject({
      id: "att-f",
      kind: "file",
      name: "notes.md",
      mime: "text/markdown",
      base64: "ZmlsZQ==",
    });
    expect(state.blocks.value).toEqual([
      {
        type: "file",
        base64: "ZmlsZQ==",
        mime_type: "text/markdown",
        filename: "notes.md",
      },
    ]);
  });

  it("rejects files of 3 MiB or more during preflight without reading them", async () => {
    const read = vi.fn(async () => dataUrl("Zg==", "application/octet-stream"));
    const state = useAttachments({ deps: { readAsDataUrl: read } });
    const file = withSize(fileOf("big.bin", "application/octet-stream"), 3 * 1024 * 1024);
    await state.addFiles([file]);
    expect(read).not.toHaveBeenCalled();
    expect(state.items.value).toHaveLength(0);
    expect(state.blocks.value).toHaveLength(0);
    expect(state.errors.value).toEqual(["chat.attachment.fileTooLarge"]);
  });

  it("rejects a file whose base64 exceeds the 4 MiB character limit", async () => {
    const oversized = dataUrl("A".repeat(MAX_FILE_BASE64_CHARS + 1), "application/octet-stream");
    const state = useAttachments({ deps: { readAsDataUrl: async () => oversized } });
    await state.addFiles([withSize(fileOf("small.bin", "application/octet-stream"), 1024)]);
    expect(state.items.value).toHaveLength(0);
    expect(state.blocks.value).toHaveLength(0);
    expect(state.errors.value).toEqual(["chat.attachment.fileTooLarge"]);
  });

  it("rejects a file above the inline limit", async () => {
    const state = useAttachments({ deps: { readAsDataUrl: async () => dataUrl("Zg==") } });
    await state.addFiles([
      withSize(fileOf("big.bin", "application/octet-stream"), MAX_FILE_BYTES + 1),
    ]);
    expect(state.items.value).toHaveLength(0);
    expect(state.errors.value).toEqual(["chat.attachment.fileTooLarge"]);
  });
});

describe("useAttachments videos", () => {
  it("probes frames, uploads the original, and exposes a video block", async () => {
    const upload = vi.fn(
      async (
        file: File,
        options: { projectId: string; onProgress: (loaded: number, total: number) => void },
      ) => {
        options.onProgress(50, 100);
        expect(file.name).toBe("demo.mp4");
        expect(options.projectId).toBe("p1");
        return uploaded();
      },
    );
    const state = useAttachments({
      projectId: "p1",
      deps: {
        probeVideo: async () => videoMeta(),
        upload,
        createId: () => "att-v",
      },
    });
    await state.addFiles([fileOf("demo.mp4", "video/mp4")]);
    expect(upload).toHaveBeenCalledOnce();
    expect(state.items.value[0]).toMatchObject({
      id: "att-v",
      kind: "video",
      status: "ready",
      attachmentId: "att-123",
      durationS: 12.5,
      width: 1280,
      height: 720,
      progress: 1,
    });
    expect(state.blocks.value).toEqual([
      {
        type: "video",
        attachment_id: "att-123",
        mime_type: "video/mp4",
        filename: "demo.mp4",
        duration_s: 12.5,
        width: 1280,
        height: 720,
        frames: [
          { type: "image", base64: "RlJBTUU0", mime_type: "image/jpeg" },
          { type: "image", base64: "RlJBTUU1", mime_type: "image/jpeg" },
        ],
      },
    ]);
    expect(state.hasReadyBlocks.value).toBe(true);
  });

  it("keeps a failed video with a retry action and recovers", async () => {
    let attempt = 0;
    const state = useAttachments({
      projectId: "p1",
      deps: {
        probeVideo: async () => videoMeta(1),
        upload: async () => {
          attempt += 1;
          if (attempt === 1) throw { code: "timeout" };
          return uploaded();
        },
        createId: () => "att-retry",
      },
    });
    await state.addFiles([fileOf("demo.mp4", "video/mp4")]);
    expect(state.items.value[0]).toMatchObject({
      status: "error",
      errorKey: "chat.attachment.uploadTimeout",
    });
    expect(state.blocks.value).toHaveLength(0);
    await state.retry("att-retry");
    expect(state.items.value[0]).toMatchObject({ status: "ready", attachmentId: "att-123" });
    expect(state.hasReadyBlocks.value).toBe(true);
  });

  it("rejects videos above the upload limit", async () => {
    const state = useAttachments({
      projectId: "p1",
      deps: { probeVideo: async () => videoMeta(), upload: async () => uploaded() },
    });
    await state.addFiles([withSize(fileOf("huge.mp4", "video/mp4"), MAX_VIDEO_BYTES + 1)]);
    expect(state.items.value).toHaveLength(0);
    expect(state.errors.value).toEqual(["chat.attachment.videoTooLarge"]);
  });

  it("keeps decode failures as typed errors", async () => {
    const state = useAttachments({
      projectId: "p1",
      deps: {
        probeVideo: async () => {
          throw new AttachmentError("chat.attachment.videoDecodeFailed");
        },
        upload: async () => uploaded(),
      },
    });
    await state.addFiles([fileOf("bad.mp4", "video/mp4")]);
    expect(state.items.value[0]).toMatchObject({
      status: "error",
      errorKey: "chat.attachment.videoDecodeFailed",
    });
  });

  it("rejects video frames that would exceed the message base64 budget", async () => {
    const chunk = "A".repeat(MAX_IMAGE_BASE64_CHARS);
    const state = useAttachments({
      projectId: "p1",
      deps: {
        compress: async () => dataUrl(chunk),
        probeVideo: async () => videoMeta(1),
        upload: async () => uploaded(),
      },
    });
    await state.addFiles([pngFile("1.png"), pngFile("2.png")]);
    await state.addFiles([fileOf("clip.mp4", "video/mp4")]);
    expect(state.items.value.filter((item) => item.kind === "video")[0]).toMatchObject({
      status: "error",
      errorKey: "chat.attachment.totalTooLarge",
    });
  });

  it("allows at most one video block", async () => {
    const state = useAttachments({
      projectId: "p1",
      deps: { probeVideo: async () => videoMeta(1), upload: async () => uploaded() },
    });
    await state.addFiles([fileOf("1.mp4", "video/mp4"), fileOf("2.mp4", "video/mp4")]);
    expect(state.items.value.filter((item) => item.kind === "video")).toHaveLength(1);
    expect(state.errors.value).toContain("chat.attachment.totalTooLarge");
  });

  it("never emits more frames than the protocol cap", async () => {
    const state = useAttachments({
      projectId: "p1",
      deps: { probeVideo: async () => videoMeta(MAX_VIDEO_FRAMES), upload: async () => uploaded() },
    });
    await state.addFiles([fileOf("demo.mp4", "video/mp4")]);
    const block = state.blocks.value[0];
    expect(block.type).toBe("video");
    if (block.type === "video") expect(block.frames).toHaveLength(MAX_VIDEO_FRAMES);
  });
});
