// SPDX-FileCopyrightText: 2026 chenxya <chenxya@ghrah.org>
//
// SPDX-License-Identifier: Apache-2.0

import { describe, expect, it } from "vitest";
import { makeAgentKey } from "./identity.js";

describe("makeAgentKey", () => {
  it("uses the stable Project and Agent IDs", () => {
    expect(makeAgentKey("proj-001", "agent-001")).toBe("proj-001:agent-001");
  });
});
