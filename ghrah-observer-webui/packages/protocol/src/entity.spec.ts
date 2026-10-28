import { describe, expect, it } from "vitest";
import fs from "node:fs";
const fixture = JSON.parse(
  fs.readFileSync(new URL("./__fixtures__/agent-entity.json", import.meta.url), "utf8"),
) as {
  requests: unknown[];
  create: unknown;
  list: unknown;
  snapshot: unknown;
  invalid_operations: unknown[];
};
import {
  AgentEntityPayloadSchema,
  AgentEntityCreatePayloadSchema,
  AgentEntityListPayloadSchema,
  AgentSnapshotSchema,
} from "./payloads/entity.js";

describe("Agent entity Python/TS wire agreement", () => {
  it("preserves all explicit operations and defaults", () => {
    for (const request of fixture.requests)
      expect(AgentEntityPayloadSchema.parse(request)).toEqual(request);
    expect(AgentEntityCreatePayloadSchema.parse(fixture.create)).toEqual(fixture.create);
    expect(AgentEntityListPayloadSchema.parse(fixture.list)).toEqual(fixture.list);
    expect(AgentSnapshotSchema.parse(fixture.snapshot)).toEqual(fixture.snapshot);
  });
  it("rejects invalid operations, identity overrides and implicit start", () => {
    for (const operation of fixture.invalid_operations) {
      expect(
        AgentEntityPayloadSchema.safeParse({ project_id: "p1", agent_id: "a1", operation }).success,
      ).toBe(false);
    }
    expect(
      AgentEntityCreatePayloadSchema.safeParse({ project_id: "p1", name: "a", cluster_id: "c1" })
        .success,
    ).toBe(false);
  });
});
