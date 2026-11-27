// SPDX-FileCopyrightText: 2026 chenxya <chenxya@ghrah.org>
//
// SPDX-License-Identifier: Apache-2.0

import { HitlRequestListViewSchema } from "@ghrah/protocol";
import { expect, it } from "vitest";
import { waitRequestFromHitl, waitStatusFromHitl } from "./wait-request.js";

it("retains the approval request facts and maps the reply to object fields", () => {
  const { items } = HitlRequestListViewSchema.parse({
    agent_id: "agent",
    items: [
      {
        promise_id: "pending",
        turn_id: "turn",
        delivery_id: null,
        ability_name: "review",
        tool_call: { path: "report.md" },
        reason: "Please review.",
        options: {},
        status: "pending",
        created_at: "",
        deadline_at: "2026-11-27T00:00:00Z",
        resolved_at: null,
        resolved_by: null,
        resolution: null,
        shell_remember: null,
      },
    ],
  });
  expect(waitRequestFromHitl(items[0], { agentId: "agent" })).toEqual({
    requestId: "pending",
    turnId: "turn",
    agentId: "agent",
    prompt: "Please review.",
    reply: {
      kind: "object",
      fields: [
        { key: "approve", label: "approve", required: true, type: { kind: "boolean" } },
        { key: "reason", label: "reason", type: { kind: "text" } },
      ],
    },
    context: { abilityName: "review", toolCall: { path: "report.md" }, shellRemember: null },
    deadline: "2026-11-27T00:00:00Z",
    status: "pending",
  });
});

it.each([
  ["approved", "responded"],
  ["rejected", "responded"],
  ["expired", "expired"],
  ["cancelled", "cancelled"],
  ["orphaned", "orphaned"],
  ["pending", "pending"],
])("maps %s as %s", (status, expected) => {
  expect(waitStatusFromHitl(status)).toBe(expected);
});
