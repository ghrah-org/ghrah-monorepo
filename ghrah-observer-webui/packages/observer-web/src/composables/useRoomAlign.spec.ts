// SPDX-FileCopyrightText: 2026 chenxya <chenxya@ghrah.org>
//
// SPDX-License-Identifier: Apache-2.0

// @vitest-environment happy-dom
import { afterEach, expect, it, vi } from "vitest";
import { initRoomAlign, roomAlign, setRoomAlign } from "./useRoomAlign";

afterEach(() => {
  vi.unstubAllGlobals();
  localStorage.clear();
  initRoomAlign();
});

it("restores only allowed alignments", () => {
  expect(roomAlign.value).toBe("self-right");
  setRoomAlign("left-all");
  expect(localStorage.getItem("ghrah-room-align")).toBe("left-all");
  initRoomAlign();
  expect(roomAlign.value).toBe("left-all");
  localStorage.setItem("ghrah-room-align", "invalid");
  initRoomAlign();
  expect(roomAlign.value).toBe("self-right");
});

it("keeps the current preference when storage rejects writes", () => {
  vi.stubGlobal("localStorage", {
    getItem: () => {
      throw new Error("disabled");
    },
    setItem: () => {
      throw new Error("disabled");
    },
  });
  initRoomAlign();
  expect(roomAlign.value).toBe("self-right");
  setRoomAlign("left-all");
  expect(roomAlign.value).toBe("left-all");
});
