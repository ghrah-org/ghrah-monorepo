// SPDX-FileCopyrightText: 2026 chenxya <chenxya@ghrah.org>
//
// SPDX-License-Identifier: Apache-2.0

// @vitest-environment happy-dom

import { describe, expect, it } from "vitest";
import {
  base64FromDataUrl,
  isSupportedImageType,
  MAX_IMAGE_BASE64_CHARS,
  MAX_TOTAL_BASE64_CHARS,
  mimeFromDataUrl,
  useAttachments,
} from "./useAttachments";

function pngFile(name = "a.png", type = "image/png"): File {
  return new File([Uint8Array.from([1, 2, 3])], name, { type });
}

function dataUrl(base64: string, mime = "image/png"): string {
  return `data:${mime};base64,${base64}`;
}

describe("attachment helpers", () => {
  it("splits base64 and mime from data URLs", () => {
    expect(base64FromDataUrl(dataUrl("aGk="))).toBe("aGk=");
    expect(base64FromDataUrl("not-a-data-url")).toBe("");
    expect(mimeFromDataUrl(dataUrl("aGk=", "image/webp"))).toBe("image/webp");
    expect(mimeFromDataUrl("not-a-data-url")).toBe("");
  });

  it("only accepts the M1 image whitelist", () => {
    for (const mime of ["image/png", "image/jpeg", "image/webp", "image/gif"]) {
      expect(isSupportedImageType(mime)).toBe(true);
    }
    expect(isSupportedImageType("image/svg+xml")).toBe(false);
    expect(isSupportedImageType("text/plain")).toBe(false);
  });
});

describe("useAttachments", () => {
  it("adds a compressed image and exposes a sendable image block", async () => {
    const state = useAttachments({
      compress: async () => dataUrl("aGk="),
      createId: () => "att-1",
    });
    await state.addFiles([pngFile()]);
    expect(state.items.value).toEqual([
      {
        id: "att-1",
        name: "a.png",
        mime: "image/png",
        size: 3,
        base64: "aGk=",
        dataUrl: dataUrl("aGk="),
      },
    ]);
    expect(state.blocks.value).toEqual([{ type: "image", base64: "aGk=", mime_type: "image/png" }]);
    expect(state.hasAttachments.value).toBe(true);
  });

  it("rejects unsupported MIME without adding an item", async () => {
    const state = useAttachments({ compress: async () => dataUrl("aGk=") });
    await state.addFiles([pngFile("note.txt", "text/plain")]);
    expect(state.items.value).toHaveLength(0);
    expect(state.errors.value).toEqual(["chat.attachment.unsupported"]);
  });

  it("rejects a single block above the base64 limit", async () => {
    const oversized = dataUrl("A".repeat(MAX_IMAGE_BASE64_CHARS + 1));
    const state = useAttachments({ compress: async () => oversized });
    await state.addFiles([pngFile()]);
    expect(state.items.value).toHaveLength(0);
    expect(state.errors.value).toEqual(["chat.attachment.tooLarge"]);
  });

  it("rejects when compression fails", async () => {
    const state = useAttachments({
      compress: async () => {
        throw new Error("boom");
      },
    });
    await state.addFiles([pngFile()]);
    expect(state.items.value).toHaveLength(0);
    expect(state.errors.value).toEqual(["chat.attachment.failed"]);
  });

  it("caps images at 4 per message", async () => {
    const state = useAttachments({
      compress: async () => dataUrl("aGk="),
      createId: (() => {
        let index = 0;
        return () => `att-${++index}`;
      })(),
    });
    await state.addFiles([pngFile("1.png"), pngFile("2.png"), pngFile("3.png"), pngFile("4.png")]);
    expect(state.items.value).toHaveLength(4);
    await state.addFiles([pngFile("5.png")]);
    expect(state.items.value).toHaveLength(4);
    expect(state.errors.value).toContain("chat.attachment.totalTooLarge");
  });

  it("enforces the total base64 budget across blocks", async () => {
    const chunk = "A".repeat(Math.floor(MAX_TOTAL_BASE64_CHARS / 2) - 4);
    const state = useAttachments({ compress: async () => dataUrl(chunk) });
    await state.addFiles([pngFile("1.png"), pngFile("2.png")]);
    expect(state.items.value).toHaveLength(2);
    await state.addFiles([pngFile("3.png")]);
    expect(state.items.value).toHaveLength(2);
    expect(state.errors.value).toContain("chat.attachment.totalTooLarge");
  });

  it("remove/clear reset items and errors", async () => {
    const state = useAttachments({
      compress: async () => dataUrl("aGk="),
      createId: () => "att-1",
    });
    await state.addFiles([pngFile(), pngFile("bad.txt", "text/plain")]);
    expect(state.items.value).toHaveLength(1);
    state.remove("att-1");
    expect(state.items.value).toHaveLength(0);
    state.clear();
    expect(state.errors.value).toHaveLength(0);
  });
});
