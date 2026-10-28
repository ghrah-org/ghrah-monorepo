import { z } from "zod";
import { AgentSpecSchema } from "./project.js";

const identifier = z.string().min(1);
export const AgentIdentitySchema = z
  .object({ project_id: identifier, agent_id: identifier })
  .strict();
export const AgentRuntimeSnapshotSchema = z
  .object({
    availability: z.enum(["online", "offline", "unknown"]),
    incarnation_id: z.string().nullable().optional().default(null),
    busy: z.boolean().nullable().optional().default(null),
    error: z.string().nullable().optional().default(null),
  })
  .strict();
export const AgentSnapshotSchema = z
  .object({
    identity: AgentIdentitySchema,
    specification: AgentSpecSchema,
    specification_revision: z.number().int(),
    runtime: AgentRuntimeSnapshotSchema,
    context: z.record(z.unknown()),
    mailbox: z.record(z.unknown()),
    abilities: z.record(z.unknown()),
    observed_at: z.string(),
  })
  .strict();
export const AgentEntityOperationSchema = z.discriminatedUnion("type", [
  z
    .object({
      type: z.literal("model.configure"),
      agent_config_name: identifier,
      expected_revision: z.number().int().min(1),
    })
    .strict(),
  z.object({ type: z.enum(["snapshot", "context.list_sessions", "abilities.list"]) }).strict(),
  z
    .object({
      type: z.enum(["start", "stop"]),
      expected_revision: z.number().int().min(1).nullable().optional().default(null),
      expected_incarnation_id: z.string().nullable().optional().default(null),
    })
    .strict(),
  z
    .object({
      type: z.literal("context.get_history"),
      session_id: identifier,
      branch_id: identifier,
      limit: z.number().int().min(1).max(1000).optional().default(100),
    })
    .strict(),
  z
    .object({
      type: z.literal("context.create_session"),
      name: z.string().nullable().optional().default(null),
      system_prompt: z.string().nullable().optional().default(null),
    })
    .strict(),
  z.object({ type: z.literal("context.activate_session"), session_id: identifier }).strict(),
  z
    .object({
      type: z.enum(["mailbox.list_inbox", "mailbox.list_outbox"]),
      limit: z.number().int().min(1).max(200).optional().default(50),
      before_seq: z.number().int().min(1).nullable().optional().default(null),
    })
    .strict(),
  z.object({ type: z.literal("mailbox.get_message"), message_id: identifier }).strict(),
  z.object({ type: z.enum(["mailbox.retry", "mailbox.resume"]), delivery_id: identifier }).strict(),
  z
    .object({
      type: z.literal("abilities.configure"),
      ability_types: z.array(identifier).min(1),
      expected_revision: z.number().int().min(1),
    })
    .strict(),
]);
export const AgentEntityPayloadSchema = AgentIdentitySchema.extend({
  operation: AgentEntityOperationSchema,
});
export const AgentEntityCreatePayloadSchema = z
  .object({
    project_id: identifier,
    name: identifier,
    cluster_id: identifier,
    start: z.boolean(),
    agent_config_name: identifier.nullable().optional().default(null),
    manifest_ref: z.string().optional().default(""),
    system_prompt: z.string().optional().default(""),
    ability_types: z.array(identifier).min(1).nullable().optional().default(null),
  })
  .strict();
export const AgentEntityListPayloadSchema = z.object({ project_id: identifier }).strict();
export type AgentIdentity = z.infer<typeof AgentIdentitySchema>;
export type AgentSnapshot = z.infer<typeof AgentSnapshotSchema>;
export type AgentRuntimeSnapshot = z.infer<typeof AgentRuntimeSnapshotSchema>;
export type AgentEntityOperation = z.input<typeof AgentEntityOperationSchema>;
export type AgentEntityPayload = z.input<typeof AgentEntityPayloadSchema>;
export type AgentEntityCreatePayload = z.input<typeof AgentEntityCreatePayloadSchema>;
export type AgentEntityListPayload = z.infer<typeof AgentEntityListPayloadSchema>;
