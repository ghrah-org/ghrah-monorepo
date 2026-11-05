// SPDX-FileCopyrightText: 2026 chenxya <chenxya@ghrah.org>
//
// SPDX-License-Identifier: Apache-2.0

import { describe, expect, it } from "vitest";
import en from "./en";
import zhCN from "./zh-CN";

type MessageTree = Record<string, unknown>;

function flatten(tree: MessageTree, prefix = ""): Record<string, string> {
  const out: Record<string, string> = {};
  for (const [key, value] of Object.entries(tree)) {
    const path = prefix ? `${prefix}.${key}` : key;
    if (typeof value === "string") out[path] = value;
    else if (value && typeof value === "object")
      Object.assign(out, flatten(value as MessageTree, path));
  }
  return out;
}

function placeholders(message: string): string[] {
  return [...message.matchAll(/\{(\w+)\}/g)].map((match) => match[1]).sort();
}

describe("locale catalogs", () => {
  it("keeps en and zh-CN keys and placeholders in parity", () => {
    const left = flatten(en as MessageTree);
    const right = flatten(zhCN as MessageTree);
    expect(Object.keys(right).sort()).toEqual(Object.keys(left).sort());
    for (const [key, message] of Object.entries(left)) {
      expect(placeholders(right[key] ?? ""), key).toEqual(placeholders(message));
    }
  });
});
