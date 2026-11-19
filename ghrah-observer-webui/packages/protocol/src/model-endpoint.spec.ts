// SPDX-FileCopyrightText: 2026 chenxya <chenxya@ghrah.org>
//
// SPDX-License-Identifier: Apache-2.0

import { describe, expect, it } from "vitest";
import { AgentConfigPayloadSchema, SpawnAgentPayloadSchema } from "./payloads/agent.js";
import { AgentRecordSchema } from "./payloads/project.js";

describe("explicit endpoint references", () => {
  it("retains request parameters through JSON serialization", () => {
    const config = {
      name: "worker",
      endpoint_name: "shared",
      model_overrides: {
        temperature: 0,
        top_p: 0,
        max_tokens: 42,
        extra_params: { vendor: false },
      },
    };
    const payload = SpawnAgentPayloadSchema.parse({ project_id: "p", config });
    expect(SpawnAgentPayloadSchema.parse(JSON.parse(JSON.stringify(payload))).config).toMatchObject(
      config,
    );
  });

  it("rejects old references even when a new reference is supplied", () => {
    const input = { name: "worker", agent_config_name: "old" };
    expect(AgentConfigPayloadSchema.safeParse(input).success).toBe(false);
    expect(AgentRecordSchema.safeParse(input).success).toBe(false);
  });
});
