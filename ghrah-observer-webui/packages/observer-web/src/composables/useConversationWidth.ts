// SPDX-FileCopyrightText: 2026 chenxya <chenxya@ghrah.org>
//
// SPDX-License-Identifier: Apache-2.0

import { ref } from "vue";

// 手动拉宽的列宽记忆。刻意不按 room 分键：同一块屏幕上的阅读宽度是
// 用户的偏好，不是某个 room 的属性，切换 room 不应该把宽度重置。
const STORAGE_KEY = "ghrah.workspace.conversation-width";

/** 与 tokens.css 里 --width-conversation 的默认值保持一致（56rem）。 */
export const CONVERSATION_WIDTH_DEFAULT_REM = 56;
/** 下限不低于输入区（46rem），保证消息流始终比输入卡片宽一档。 */
export const CONVERSATION_WIDTH_MIN_REM = 48;
/** 上限给到超宽屏；容器更窄时由 w-full 收住，不会撑出横向滚动。 */
export const CONVERSATION_WIDTH_MAX_REM = 88;
/** 方向键每按一次的步长。 */
export const CONVERSATION_WIDTH_STEP_REM = 2;

export const conversationWidthRem = ref(CONVERSATION_WIDTH_DEFAULT_REM);

function readStoredWidth(): string | null {
  try {
    return globalThis.localStorage?.getItem(STORAGE_KEY) ?? null;
  } catch {
    return null; // 嵌入/隐私上下文可能禁用存储
  }
}

function writeStoredWidth(value: number) {
  try {
    globalThis.localStorage?.setItem(STORAGE_KEY, String(value));
  } catch {
    // 存不下就只保留内存状态，交互不受影响
  }
}

export function normalizeConversationWidth(value: string | number | null | undefined): number {
  // 缺失或空白表示没有偏好。Number(null) === 0，直接夹紧会被下限收成最小值。
  if (value == null || (typeof value === "string" && value.trim() === "")) {
    return CONVERSATION_WIDTH_DEFAULT_REM;
  }
  const parsed = typeof value === "number" ? value : Number(value);
  if (!Number.isFinite(parsed)) return CONVERSATION_WIDTH_DEFAULT_REM;
  const clamped = Math.min(
    CONVERSATION_WIDTH_MAX_REM,
    Math.max(CONVERSATION_WIDTH_MIN_REM, parsed),
  );
  // 两位小数足够，避免拖拽产生的浮点噪声写进存储。
  return Number(clamped.toFixed(2));
}

/** 写到根元素的内联样式：内联声明优先于 tokens 层，不必改 tokens.css。 */
function applyWidth(value: number) {
  document.documentElement.style.setProperty("--width-conversation", `${value}rem`);
}

export function initConversationWidth() {
  conversationWidthRem.value = normalizeConversationWidth(readStoredWidth());
  applyWidth(conversationWidthRem.value);
}

export function setConversationWidth(value: number) {
  conversationWidthRem.value = normalizeConversationWidth(value);
  applyWidth(conversationWidthRem.value);
  // 只在提交时写盘：拖拽过程走内联变量，不碰 localStorage。
  writeStoredWidth(conversationWidthRem.value);
}

export function resetConversationWidth() {
  setConversationWidth(CONVERSATION_WIDTH_DEFAULT_REM);
}

/**
 * 拖拽过程中的预览：只改内联变量，不写盘也不触发响应式更新。
 * 这样每帧不引起组件重渲染，拖起来才跟手。
 */
export function previewConversationWidth(value: number) {
  applyWidth(normalizeConversationWidth(value));
}
