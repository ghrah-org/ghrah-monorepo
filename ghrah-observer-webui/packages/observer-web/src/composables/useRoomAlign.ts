// SPDX-FileCopyrightText: 2026 chenxya <chenxya@ghrah.org>
//
// SPDX-License-Identifier: Apache-2.0

import { ref } from "vue";

export type RoomAlign = "self-right" | "left-all";
const STORAGE_KEY = "ghrah-room-align";
export const roomAlign = ref<RoomAlign>("self-right");

export function normalizeRoomAlign(value: unknown): RoomAlign {
  return value === "left-all" ? "left-all" : "self-right";
}

export function initRoomAlign() {
  try {
    roomAlign.value = normalizeRoomAlign(globalThis.localStorage?.getItem(STORAGE_KEY));
  } catch {
    roomAlign.value = "self-right";
  }
}

export function setRoomAlign(value: unknown) {
  roomAlign.value = normalizeRoomAlign(value);
  try {
    globalThis.localStorage?.setItem(STORAGE_KEY, roomAlign.value);
  } catch {
    // 禁用持久化时仍可调整当前会话的展示。
  }
}
