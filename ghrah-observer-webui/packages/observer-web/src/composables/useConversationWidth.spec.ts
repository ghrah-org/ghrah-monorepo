// SPDX-FileCopyrightText: 2026 chenxya <chenxya@ghrah.org>
//
// SPDX-License-Identifier: Apache-2.0

// @vitest-environment happy-dom

import { beforeEach, describe, expect, it } from "vitest";
import {
  CONVERSATION_WIDTH_DEFAULT_REM,
  CONVERSATION_WIDTH_MAX_REM,
  CONVERSATION_WIDTH_MIN_REM,
  conversationWidthRem,
  initConversationWidth,
  normalizeConversationWidth,
  previewConversationWidth,
  resetConversationWidth,
  setConversationWidth,
} from "./useConversationWidth";

const KEY = "ghrah.workspace.conversation-width";

function appliedWidth(): string {
  return document.documentElement.style.getPropertyValue("--width-conversation");
}

describe("normalizeConversationWidth", () => {
  it("falls back to the default when there is no preference", () => {
    expect(normalizeConversationWidth(null)).toBe(CONVERSATION_WIDTH_DEFAULT_REM);
    expect(normalizeConversationWidth(undefined)).toBe(CONVERSATION_WIDTH_DEFAULT_REM);
    expect(normalizeConversationWidth("")).toBe(CONVERSATION_WIDTH_DEFAULT_REM);
    expect(normalizeConversationWidth("   ")).toBe(CONVERSATION_WIDTH_DEFAULT_REM);
    expect(normalizeConversationWidth("abc")).toBe(CONVERSATION_WIDTH_DEFAULT_REM);
  });

  it("clamps to the allowed range", () => {
    expect(normalizeConversationWidth(10)).toBe(CONVERSATION_WIDTH_MIN_REM);
    expect(normalizeConversationWidth(1000)).toBe(CONVERSATION_WIDTH_MAX_REM);
  });

  it("accepts numeric strings and trims float noise", () => {
    expect(normalizeConversationWidth("64")).toBe(64);
    expect(normalizeConversationWidth(63.333333)).toBe(63.33);
  });

  it("never goes narrower than the composer width", () => {
    expect(CONVERSATION_WIDTH_MIN_REM).toBeGreaterThan(46);
  });
});

describe("conversation width storage", () => {
  beforeEach(() => {
    localStorage.clear();
    document.documentElement.style.removeProperty("--width-conversation");
  });

  it("applies the default when nothing is stored", () => {
    initConversationWidth();
    expect(conversationWidthRem.value).toBe(CONVERSATION_WIDTH_DEFAULT_REM);
    expect(appliedWidth()).toBe(`${CONVERSATION_WIDTH_DEFAULT_REM}rem`);
  });

  it("restores the stored width on init", () => {
    localStorage.setItem(KEY, "72");
    initConversationWidth();
    expect(conversationWidthRem.value).toBe(72);
    expect(appliedWidth()).toBe("72rem");
  });

  it("ignores a corrupted stored value", () => {
    localStorage.setItem(KEY, "not-a-number");
    initConversationWidth();
    expect(conversationWidthRem.value).toBe(CONVERSATION_WIDTH_DEFAULT_REM);
  });

  it("persists the committed width", () => {
    setConversationWidth(80);
    expect(localStorage.getItem(KEY)).toBe("80");
    expect(appliedWidth()).toBe("80rem");
  });

  it("does not touch storage while previewing", () => {
    localStorage.setItem(KEY, "60");
    previewConversationWidth(74);
    expect(appliedWidth()).toBe("74rem");
    expect(localStorage.getItem(KEY)).toBe("60");
  });

  it("resets back to the default", () => {
    setConversationWidth(80);
    resetConversationWidth();
    expect(localStorage.getItem(KEY)).toBe(String(CONVERSATION_WIDTH_DEFAULT_REM));
    expect(appliedWidth()).toBe(`${CONVERSATION_WIDTH_DEFAULT_REM}rem`);
  });

  it("keys the preference globally rather than per room", () => {
    // 记忆不随 room 变化：键名里不含 project/room 标识。
    setConversationWidth(70);
    expect(KEY).toBe("ghrah.workspace.conversation-width");
    expect(Object.keys(localStorage)).toEqual([KEY]);
  });
});
