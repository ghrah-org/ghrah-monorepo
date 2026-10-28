import { z } from "zod";
import { BranchInfoPayloadSchema } from "./session.js";

export const AgentConfigPayloadSchema = z
  .object({
    name: z.string(),
    agent_id: z.string().optional(),
    endpoint_name: z.string().nullable().optional(),
    description: z.string().optional().default(""),
    system_prompt: z.string().optional().default(""),
    max_iterations: z.number().int().optional().default(10),
    communication_timeout: z.number().optional(),
    window: z.record(z.unknown()).nullable().optional(),
    context: z.record(z.unknown()).nullable().optional(),
    model_overrides: z.record(z.unknown()).nullable().optional(),
  })
  .strict();

export const AbilityDefinitionPayloadSchema = z.object({
  ability_type: z.string(),
  params: z.record(z.unknown()).optional().default({}),
});

export const SpawnAgentPayloadSchema = z
  .object({
    validate_model: z.boolean().optional(),
    project_id: z.string(),
    cluster_id: z.string().optional().default(""),
    config: AgentConfigPayloadSchema,
    abilities: z.array(AbilityDefinitionPayloadSchema).nullable().optional(),
    manifest_ref: z.string().nullable().optional(),
  })
  .strict();

export const TerminateAgentPayloadSchema = z.object({
  project_id: z.string(),
  agent_id: z.string(),
  name: z.string(),
});

export const AgentResetPayloadSchema = z.object({
  project_id: z.string(),
  agent_id: z.string(),
});

export const SendMessagePayloadSchema = z.object({
  project_id: z.string(),
  agent_id: z.string(),
  target: z.string(),
  content: z.string(),
  sender: z.string().optional().default("user"),
  timeout: z.number().nullable().optional(),
  metadata: z.record(z.unknown()).nullable().optional(),
});

export const BroadcastMessagePayloadSchema = z.object({
  project_id: z.string(),
  content: z.string(),
  sender: z.string().optional().default("user"),
});

export const RegisterAbilityPayloadSchema = z.object({
  project_id: z.string(),
  agent_id: z.string(),
  agent_name: z.string(),
  ability: AbilityDefinitionPayloadSchema,
});

export const UnregisterAbilityPayloadSchema = z.object({
  project_id: z.string(),
  agent_id: z.string(),
  agent_name: z.string(),
  ability_name: z.string(),
});

export const ListAgentsPayloadSchema = z.object({
  project_id: z.string(),
});

export const HealthCheckPayloadSchema = z.object({});

export const DelegatePayloadSchema = z.object({
  project_id: z.string(),
  from_agent_id: z.string(),
  to_agent_id: z.string(),
  from_agent: z.string(),
  to_agent: z.string(),
  content: z.string(),
  timeout: z.number().nullable().optional(),
});

export const GetAgentInfoPayloadSchema = z.object({
  project_id: z.string(),
  agent_id: z.string(),
  name: z.string(),
});

export const AgentCompactContextPayloadSchema = z.object({
  project_id: z.string(),
  agent_id: z.string(),
  agent_name: z.string(),
  cluster_id: z.string().optional().default(""),
});

export const SubscribePayloadSchema = z.object({
  agent_names: z.array(z.string()).nullable().optional(),
  event_types: z.array(z.string()).nullable().optional(),
});

export const UnsubscribePayloadSchema = z.object({
  agent_names: z.array(z.string()).nullable().optional(),
  event_types: z.array(z.string()).nullable().optional(),
});

export const ExecuteAbilityPayloadSchema = z.object({
  request_id: z.string(),
  agent_name: z.string(),
  ability_name: z.string(),
  tool_args: z.record(z.unknown()).optional().default({}),
});

export const HITLResponsePayloadSchema = z
  .object({
    promise_id: z.string().optional().default(""),
    agent_name: z.string().optional().default(""),
    ability_name: z.string().optional().default(""),
    tool_call_id: z.string().optional().default(""),
    approved: z.boolean(),
    reason: z.string().nullable().optional(),
    result: z.unknown().nullable().optional(),
  })
  .refine((p) => Boolean(p.promise_id) || (p.agent_name && p.ability_name && p.tool_call_id), {
    message:
      "hitl_response requires either promise_id or the complete triplet " +
      "(agent_name + ability_name + tool_call_id); got neither",
  });

export const HITLResolvedPayloadSchema = z.object({
  project_id: z.string().optional().default(""),
  agent_id: z.string().optional().default(""),
  cluster_id: z.string().optional().default(""),
  promise_id: z.string(),
  agent_name: z.string(),
  ability_name: z.string(),
  tool_call_id: z.string().optional().default(""),
  status: z.enum(["approved", "rejected", "timeout"]),
});

export const InitClusterPayloadSchema = z.object({
  cluster_id: z.string(),
  config: z.record(z.unknown()).optional().default({}),
});

export const ShutdownClusterPayloadSchema = z.object({
  cluster_id: z.string(),
  config: z.record(z.unknown()).optional().default({}),
});

export const ClusterStatusPayloadSchema = z.object({
  cluster_id: z.string(),
});

export const ListClustersPayloadSchema = z.object({});

export const ClusterInfoPayloadSchema = z.object({
  cluster_id: z.string(),
  active_agents: z.number(),
  status: z.string(),
  bound: z.boolean(),
});

export const ListClustersResultPayloadSchema = z.object({
  clusters: z.array(ClusterInfoPayloadSchema),
});

export const AgentSpawnedPayloadSchema = z.object({
  project_id: z.string().optional().default(""),
  cluster_id: z.string().optional().default(""),
  name: z.string(),
  agent_id: z.string().optional().default(""),
  incarnation_id: z.string().optional().default(""),
  recovery_mode: z.string().optional().default(""),
  config: AgentConfigPayloadSchema,
});

export const AgentTerminatedPayloadSchema = z.object({
  project_id: z.string().optional().default(""),
  cluster_id: z.string().optional().default(""),
  name: z.string(),
  agent_id: z.string().optional().default(""),
  incarnation_id: z.string().optional().default(""),
});

export const ContentBlockSchema = z.discriminatedUnion("type", [
  z.object({
    type: z.literal("text"),
    text: z.string(),
  }),
  z.object({
    type: z.literal("reasoning"),
    reasoning: z.string(),
    incomplete: z.boolean().optional().default(false),
  }),
  z.object({
    type: z.literal("image"),
    url: z.string().nullable().optional(),
    base64: z.string().nullable().optional(),
    mime_type: z.string().nullable().optional(),
  }),
  z.object({
    type: z.literal("audio"),
    data: z.string(),
    mime_type: z.string(),
  }),
  z.object({
    type: z.literal("file"),
    url: z.string().nullable().optional(),
    base64: z.string().nullable().optional(),
    mime_type: z.string().nullable().optional(),
    filename: z.string().nullable().optional(),
  }),
  z.object({
    type: z.literal("tool_call"),
    id: z.string(),
    name: z.string(),
    arguments: z.record(z.unknown()),
  }),
  z.object({
    type: z.literal("tool_result"),
    tool_call_id: z.string(),
    name: z.string().nullable().optional(),
    content: z.string(),
    success: z.boolean().optional().default(true),
    error: z.string().nullable().optional(),
  }),
  z.object({
    type: z.literal("error"),
    error_type: z.string(),
    message: z.string(),
    details: z.record(z.unknown()).nullable().optional(),
  }),
]);

export type ContentBlock = z.infer<typeof ContentBlockSchema>;

export const ChatMessageWireSchema = z.object({
  role: z.enum(["system", "user", "ai", "tool"]).default("user"),
  content_blocks: z.array(ContentBlockSchema).optional().default([]),
  source: z.string().nullable().optional(),
  metadata: z.record(z.unknown()).optional().default({}),
});
export type ChatMessageWire = z.infer<typeof ChatMessageWireSchema>;

export const ActionResultItemSchema = z.object({
  ability_name: z.string(),
  action_result: z
    .object({
      outcome: z.enum(["success", "failure", "needs_input", "delegate"]).optional(),
      data: z.record(z.unknown()).optional().default({}),
      next_action_hint: z.string().nullable().optional(),
    })
    .nullable()
    .optional(),
});
export type ActionResultItem = z.infer<typeof ActionResultItemSchema>;

export const ActionNodeSchema = z.object({
  id: z.string().optional().default(""),
  parent_id: z.string().nullable().optional(),
  agent_name: z.string().optional().default(""),
  timestamp: z.string().optional(),
  iteration: z.number().int().optional().default(0),
  ability_names: z.array(z.string()).optional().default([]),
  agent_state: z.record(z.unknown()).optional().default({}),
  messages_delta: z.array(ChatMessageWireSchema).optional().default([]),
  messages_snapshot: z.array(ChatMessageWireSchema).nullable().optional(),
  is_snapshot: z.boolean().optional().default(false),
  action_results: z.array(ActionResultItemSchema).optional().default([]),
  metadata: z.record(z.unknown()).optional().default({}),
  session_id: z.string().optional().default(""),
  created_on_branch_id: z.string().optional().default(""),
});
export type ActionNode = z.infer<typeof ActionNodeSchema>;

export const AgentResponsePayloadSchema = z.object({
  project_id: z.string().optional().default(""),
  agent_id: z.string().optional().default(""),
  cluster_id: z.string().optional().default(""),
  sender: z.string(),
  recipient: z.string(),
  content: z.string(),
  content_blocks: z.array(ContentBlockSchema).nullable().optional(),
  message_type: z.string().optional().default("result"),
  metadata: z.record(z.unknown()).optional().default({}),
});

export const ActionChainUpdatedPayloadSchema = z.object({
  project_id: z.string().optional().default(""),
  agent_id: z.string().optional().default(""),
  cluster_id: z.string().optional().default(""),
  agent_name: z.string(),
  node: ActionNodeSchema.optional().default({}),
  branch: BranchInfoPayloadSchema.nullable().optional(),
});

export const ContextUsageUpdatedPayloadSchema = z.object({
  project_id: z.string().optional().default(""),
  agent_id: z.string().optional().default(""),
  cluster_id: z.string().optional().default(""),
  agent_name: z.string(),
  phase: z.string(),
  occupied_tokens: z.number().int().nullable().optional(),
  basis: z.string(),
  budget_tokens: z.number().int().optional().default(0),
  // declared / agentconf / model_table / default / vendor
  budget_source: z.string().nullable().optional(),
  compact_threshold: z.number().nullable().optional(),
  real_input_tokens: z.number().int().nullable().optional(),
  real_output_tokens: z.number().int().nullable().optional(),
  real_cache_read_tokens: z.number().int().nullable().optional(),
  real_cache_write_tokens: z.number().int().nullable().optional(),
  compaction: z.record(z.unknown()).nullable().optional(),
  iteration: z.number().int().nullable().optional(),
});

export const AgentErrorPayloadSchema = z.object({
  project_id: z.string().optional().default(""),
  agent_id: z.string().optional().default(""),
  cluster_id: z.string().optional().default(""),
  agent_name: z.string(),
  error: z.string(),
});

export const HealthStatusPayloadSchema = z.object({
  status: z.record(z.boolean()),
});

export const AbilityResultPayloadSchema = z.object({
  project_id: z.string().optional().default(""),
  agent_id: z.string().optional().default(""),
  cluster_id: z.string().optional().default(""),
  request_id: z.string(),
  agent_name: z.string(),
  ability_name: z.string(),
  success: z.boolean(),
  result: z.unknown().nullable().optional(),
  error: z.string().nullable().optional(),
});

export const HITLRequestPayloadSchema = z.object({
  project_id: z.string().optional().default(""),
  agent_id: z.string().optional().default(""),
  cluster_id: z.string().optional().default(""),
  promise_id: z.string(),
  agent_name: z.string(),
  ability_name: z.string(),
  tool_args: z.record(z.unknown()).optional().default({}),
  context: z.record(z.unknown()).optional().default({}),
  reason: z.string().optional().default(""),
});

export type AgentConfigPayload = z.infer<typeof AgentConfigPayloadSchema>;
export type AbilityDefinitionPayload = z.infer<typeof AbilityDefinitionPayloadSchema>;
export type SpawnAgentPayload = z.infer<typeof SpawnAgentPayloadSchema>;
export type TerminateAgentPayload = z.infer<typeof TerminateAgentPayloadSchema>;
export type AgentResetPayload = z.infer<typeof AgentResetPayloadSchema>;
export type SendMessagePayload = z.infer<typeof SendMessagePayloadSchema>;
export type BroadcastMessagePayload = z.infer<typeof BroadcastMessagePayloadSchema>;
export type RegisterAbilityPayload = z.infer<typeof RegisterAbilityPayloadSchema>;
export type UnregisterAbilityPayload = z.infer<typeof UnregisterAbilityPayloadSchema>;
export type ListAgentsPayload = z.infer<typeof ListAgentsPayloadSchema>;
export type HealthCheckPayload = z.infer<typeof HealthCheckPayloadSchema>;
export type DelegatePayload = z.infer<typeof DelegatePayloadSchema>;
export type GetAgentInfoPayload = z.infer<typeof GetAgentInfoPayloadSchema>;
export type AgentCompactContextPayload = z.infer<typeof AgentCompactContextPayloadSchema>;
export type SubscribePayload = z.infer<typeof SubscribePayloadSchema>;
export type UnsubscribePayload = z.infer<typeof UnsubscribePayloadSchema>;
export type ExecuteAbilityPayload = z.infer<typeof ExecuteAbilityPayloadSchema>;
export type HITLResponsePayload = z.infer<typeof HITLResponsePayloadSchema>;
export type InitClusterPayload = z.infer<typeof InitClusterPayloadSchema>;
export type ShutdownClusterPayload = z.infer<typeof ShutdownClusterPayloadSchema>;
export type ClusterStatusPayload = z.infer<typeof ClusterStatusPayloadSchema>;
export type ListClustersPayload = z.infer<typeof ListClustersPayloadSchema>;
export type ClusterInfoPayload = z.infer<typeof ClusterInfoPayloadSchema>;
export type ListClustersResultPayload = z.infer<typeof ListClustersResultPayloadSchema>;

export type AgentSpawnedPayload = z.infer<typeof AgentSpawnedPayloadSchema>;
export type AgentTerminatedPayload = z.infer<typeof AgentTerminatedPayloadSchema>;
export type AgentResponsePayload = z.infer<typeof AgentResponsePayloadSchema>;
export type ActionChainUpdatedPayload = z.infer<typeof ActionChainUpdatedPayloadSchema>;
export type ContextUsageUpdatedPayload = z.infer<typeof ContextUsageUpdatedPayloadSchema>;
export type AgentErrorPayload = z.infer<typeof AgentErrorPayloadSchema>;
export type HealthStatusPayload = z.infer<typeof HealthStatusPayloadSchema>;
export type AbilityResultPayload = z.infer<typeof AbilityResultPayloadSchema>;
export type HITLRequestPayload = z.infer<typeof HITLRequestPayloadSchema>;
export type HITLResolvedPayload = z.infer<typeof HITLResolvedPayloadSchema>;
