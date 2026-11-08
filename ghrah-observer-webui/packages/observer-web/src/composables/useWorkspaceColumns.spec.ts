// SPDX-FileCopyrightText: 2026 chenxya <chenxya@ghrah.org>
//
// SPDX-License-Identifier: Apache-2.0

import { describe, expect, it } from "vitest";
import { readWorkspaceColumnMode } from "./useWorkspaceColumns";

function matchesAt(rem: number) {
  return (query: string) => {
    const min = Number(/min-width:\s*([\d.]+)rem/.exec(query)?.[1] ?? NaN);
    return Number.isFinite(min) && rem >= min;
  };
}

describe("readWorkspaceColumnMode", () => {
  it("uses the wide columns at 1440 and above", () => {
    expect(readWorkspaceColumnMode(matchesAt(90))).toBe("wide");
    expect(readWorkspaceColumnMode(matchesAt(100))).toBe("wide");
  });

  it("uses the middle columns from 1200 up to 1440", () => {
    expect(readWorkspaceColumnMode(matchesAt(75))).toBe("mid");
    expect(readWorkspaceColumnMode(matchesAt(89))).toBe("mid");
  });

  it("forces the context rail from 1024 up to 1200", () => {
    expect(readWorkspaceColumnMode(matchesAt(64))).toBe("rail");
    expect(readWorkspaceColumnMode(matchesAt(74))).toBe("rail");
  });

  it("keeps room and context from sharing a column below 1024", () => {
    expect(readWorkspaceColumnMode(matchesAt(60))).toBe("exclusive");
    expect(readWorkspaceColumnMode(matchesAt(40))).toBe("exclusive");
  });
});
