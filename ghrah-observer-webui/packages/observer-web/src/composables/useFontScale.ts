// SPDX-FileCopyrightText: 2026 chenxya <chenxya@ghrah.org>
//
// SPDX-License-Identifier: Apache-2.0

import { computed, ref } from "vue";

const STORAGE_KEY = "ghrah-font-scale";

export const FONT_SCALE_MIN = 0.9;
export const FONT_SCALE_MAX = 1.1;
export const FONT_SCALE_STEP = 0.05;

export const fontScale = ref(1);
export const fontScalePercent = computed(() => Math.round(fontScale.value * 100));

function readStoredScale(): string | null {
  try {
    return globalThis.localStorage?.getItem(STORAGE_KEY) ?? null;
  } catch {
    return null; // 嵌入/隐私上下文可能禁用存储
  }
}

function writeStoredScale(value: number) {
  try {
    globalThis.localStorage?.setItem(STORAGE_KEY, String(value));
  } catch {
    // 存不下就只保留内存状态，交互不受影响
  }
}

function normalizeScale(value: string | number | null | undefined): number {
  // 缺失或空白表示没有偏好。Number(null) === 0，若直接夹紧会被下限收成 0.9。
  if (value == null || (typeof value === "string" && value.trim() === "")) return 1;
  const parsed = typeof value === "number" ? value : Number(value);
  if (!Number.isFinite(parsed)) return 1;
  const clamped = Math.min(FONT_SCALE_MAX, Math.max(FONT_SCALE_MIN, parsed));
  // Snap to the step grid and round away float noise (e.g. 1.1 -> "110.00000000000001%").
  return Number((Math.round(clamped / FONT_SCALE_STEP) * FONT_SCALE_STEP).toFixed(2));
}

// --font-scale feeds the html root font-size (see styles/base.css), so it scales the whole UI.
function applyScale(value: number) {
  document.documentElement.style.setProperty("--font-scale", String(value));
}

export function initFontScale() {
  fontScale.value = normalizeScale(readStoredScale());
  applyScale(fontScale.value);
}

export function setFontScale(value: string | number) {
  fontScale.value = normalizeScale(value);
  writeStoredScale(fontScale.value);
  applyScale(fontScale.value);
}
