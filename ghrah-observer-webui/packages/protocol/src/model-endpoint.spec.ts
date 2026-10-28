import { describe, expect, it } from "vitest";
import { AgentConfigPayloadSchema, SpawnAgentPayloadSchema } from "./payloads/agent.js";
import { AgentSpecSchema } from "./payloads/project.js";

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
    for (const endpoint_name of [undefined, "new"]) {
      const input = { name: "worker", endpoint_name, agent_config_name: "old" };
      expect(AgentConfigPayloadSchema.safeParse(input).success).toBe(false);
      expect(AgentSpecSchema.safeParse({ ...input, cluster_id: "c" }).success).toBe(false);
    }
  });
});
