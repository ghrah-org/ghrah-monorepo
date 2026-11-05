import { z } from "zod";

// ─── Manifest CRUD Payloads ───

export const ManifestListPayloadSchema = z.object({
  namespace: z.string().nullable().optional(),
});

export const ManifestGetPayloadSchema = z.object({
  full_name: z.string(),
});

export const ManifestPutPayloadSchema = z.object({
  full_name: z.string(),
  content: z.string(),
  overwrite: z.boolean().optional().default(false),
});

export const ManifestDeletePayloadSchema = z.object({
  full_name: z.string(),
});

export const ManifestValidatePayloadSchema = z.object({
  content: z.string(),
  manifest_type: z.string(),
});

export const ManifestResolvePayloadSchema = z.object({
  agent_full_name: z.string(),
  runtime_name: z.string().nullable().optional(),
  content: z.string().nullable().optional(),
});

// ─── Manifest Response Payloads (aligned with ghrah-protocol) ───

export const ManifestGetAbilityResponsePayloadSchema = z.object({
  manifest: z.record(z.unknown()),
  source: z.string(),
});

export const ManifestGetAgentResponsePayloadSchema = ManifestGetAbilityResponsePayloadSchema;

export const ManifestListErrorPayloadSchema = z.object({
  full_name: z.string(),
  error: z.string(),
});

export const ManifestListAbilitiesResponsePayloadSchema = z.object({
  abilities: z.array(z.record(z.unknown())),
  errors: z.array(ManifestListErrorPayloadSchema).default([]),
});

export const ManifestValidateResponsePayloadSchema = z.object({
  valid: z.boolean(),
  errors: z.array(z.string()),
});

export const ManifestResolveAgentResponsePayloadSchema = z.object({
  agent_full_name: z.string(),
  ability_source: z.enum(["legacy", "builtin"]),
  input_source: z.enum(["stored", "draft"]),
  config: z.record(z.unknown()),
  abilities: z.array(
    z.object({
      ability_name: z.string(),
      tool_schema: z.record(z.unknown()).nullable(),
      permissions: z.record(z.unknown()),
      implementation: z.record(z.unknown()),
      hooks: z.record(z.unknown()),
    }),
  ),
});

// ─── Manifest Event Payloads ───

export const ManifestAbilityEventPayloadSchema = z.object({
  full_name: z.string(),
  namespace: z.string(),
  manifest: z.record(z.unknown()).nullable().optional(),
  source: z.string().nullable().optional(),
});

export const ManifestAgentEventPayloadSchema = z.object({
  full_name: z.string(),
  namespace: z.string(),
  manifest: z.record(z.unknown()).nullable().optional(),
  source: z.string().nullable().optional(),
});

export type ManifestListPayload = z.infer<typeof ManifestListPayloadSchema>;
export type ManifestGetPayload = z.infer<typeof ManifestGetPayloadSchema>;
export type ManifestPutPayload = z.infer<typeof ManifestPutPayloadSchema>;
export type ManifestDeletePayload = z.infer<typeof ManifestDeletePayloadSchema>;
export type ManifestValidatePayload = z.infer<typeof ManifestValidatePayloadSchema>;
export type ManifestResolvePayload = z.infer<typeof ManifestResolvePayloadSchema>;
export type ManifestGetAbilityResponsePayload = z.infer<
  typeof ManifestGetAbilityResponsePayloadSchema
>;
export type ManifestGetAgentResponsePayload = z.infer<typeof ManifestGetAgentResponsePayloadSchema>;
export type ManifestListErrorPayload = z.infer<typeof ManifestListErrorPayloadSchema>;
export type ManifestListAbilitiesResponsePayload = z.infer<
  typeof ManifestListAbilitiesResponsePayloadSchema
>;
export type ManifestValidateResponsePayload = z.infer<typeof ManifestValidateResponsePayloadSchema>;
export type ManifestResolveAgentResponsePayload = z.infer<
  typeof ManifestResolveAgentResponsePayloadSchema
>;
export type ManifestAbilityEventPayload = z.infer<typeof ManifestAbilityEventPayloadSchema>;
export type ManifestAgentEventPayload = z.infer<typeof ManifestAgentEventPayloadSchema>;
