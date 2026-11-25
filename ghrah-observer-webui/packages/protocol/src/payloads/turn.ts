// SPDX-FileCopyrightText: 2026 chenxya <chenxya@ghrah.org>
//
// SPDX-License-Identifier: Apache-2.0

import { z } from "zod";

import { ShellRememberOptionsSchema } from "../hitl.generated.js";
import { ActionNodeSchema } from "./agent.js";

const TurnPayloadSchema = z.object({
  project_id: z.string().optional().default(""),
  agent_id: z.string().optional().default(""),
  agent_name: z.string(),
  turn_id: z.string(),
  session_id: z.string(),
});

export const TurnOpenedPayloadSchema = TurnPayloadSchema.extend({
  status: z.string().optional().default("open"),
  opener_delivery_id: z.string().nullable().optional(),
});

export const TurnWaitingHitlPayloadSchema = TurnPayloadSchema.extend({
  promise_id: z.string(),
});

export const TurnClosedPayloadSchema = TurnPayloadSchema.extend({
  status: z.string(),
  closing_kind: z.string(),
  end_node_id: z.string().nullable().optional(),
});

export type TurnOpenedPayload = z.infer<typeof TurnOpenedPayloadSchema>;
export type TurnWaitingHitlPayload = z.infer<typeof TurnWaitingHitlPayloadSchema>;
export type TurnClosedPayload = z.infer<typeof TurnClosedPayloadSchema>;

const RollbackTargetSchema = z.enum(["base", "node"]);

export const TurnAbortPayloadSchema = z.strictObject({
  project_id: z.string().min(1),
  agent_id: z.string().min(1),
  turn_id: z.string().min(1).nullable().default(null),
  session_id: z.string().min(1).nullable().default(null),
  reason: z.string().default(""),
});

export const TurnRollbackPayloadSchema = z
  .strictObject({
    project_id: z.string().min(1),
    agent_id: z.string().min(1),
    turn_id: z.string().min(1),
    target: RollbackTargetSchema.default("base"),
    node_id: z.string().min(1).nullable().default(null),
  })
  .refine(({ target, node_id }) => (target === "node") === (node_id !== null), {
    path: ["node_id"],
    message: "node_id is required exactly when target is node",
  });

export const TurnAbortViewSchema = z.strictObject({
  turn_id: z.string(),
  status: z.string(),
  closing_kind: z.string().nullable().default(null),
  aborted: z.boolean().default(false),
  already_terminal: z.boolean().default(false),
  result_pending: z.boolean().default(false),
  cancelled_hitl: z.number().int().default(0),
  released_deliveries: z.number().int().default(0),
  incomplete_node_id: z.string().nullable().default(null),
  recovery_branch_id: z.string().nullable().default(null),
  opener_delivery_id: z.string().nullable().default(null),
});

export const TurnRollbackViewSchema = z.strictObject({
  turn_id: z.string(),
  session_id: z.string(),
  status: z.string(),
  closing_kind: z.string(),
  target: RollbackTargetSchema,
  target_node_id: z.string(),
  branch_id: z.string(),
  parent_branch_id: z.string().nullable().default(null),
  released_delivery_ids: z.array(z.string()).default([]),
  already_applied: z.boolean().default(false),
  rolled_back_at: z.string(),
});

export const TurnRollbackPreviewViewSchema = z.strictObject({
  turn_id: z.string(),
  session_id: z.string(),
  target: RollbackTargetSchema,
  target_node_id: z.string(),
  target_branch_id: z.string(),
  base_node_id: z.string().nullable().default(null),
  start_node_id: z.string().nullable().default(null),
  end_node_id: z.string().nullable().default(null),
  truncated_node_count: z.number().int().default(0),
  crosses_compact: z.boolean().default(false),
  target_is_compact: z.boolean().default(false),
  head_unchanged: z.boolean().default(false),
  released_delivery_ids: z.array(z.string()).default([]),
});

export type TurnAbortPayload = z.input<typeof TurnAbortPayloadSchema>;
export type TurnRollbackPayload = z.input<typeof TurnRollbackPayloadSchema>;
export type TurnAbortView = z.infer<typeof TurnAbortViewSchema>;
export type TurnRollbackView = z.infer<typeof TurnRollbackViewSchema>;
export type TurnRollbackPreviewView = z.infer<typeof TurnRollbackPreviewViewSchema>;

const TurnFieldsSchema = z.object({
  turn_id: z.string(),
  agent_name: z.string(),
  session_id: z.string(),
  status: z.string(),
  closing_kind: z.string().nullable(),
  base_node_id: z.string().nullable(),
  start_node_id: z.string().nullable(),
  end_node_id: z.string().nullable(),
  branch_id: z.string().nullable(),
  end_branch_id: z.string().nullable(),
  opener_delivery_id: z.string().nullable(),
  opened_at: z.string(),
  waiting_since: z.string().nullable(),
  closed_at: z.string().nullable(),
  abort_reason: z.string().nullable(),
  incomplete_node_id: z.string().nullable(),
  recovery_branch_id: z.string().nullable(),
});

export const TurnListSchema = z.object({
  agent_id: z.string(),
  items: z.array(TurnFieldsSchema.extend({ injections: z.number().int() })),
});

export const TurnStatsSchema = z.object({
  steps: z.number().int().nonnegative(),
  node_count: z.number().int().nonnegative(),
  token_input: z.number().int().nonnegative(),
  token_output: z.number().int().nonnegative(),
  token_total: z.number().int().nonnegative(),
  token_cache_read: z.number().int().nonnegative(),
  token_cache_write: z.number().int().nonnegative(),
  cache_hit_rate: z.number().nonnegative().nullable(),
  duration_ms: z.number().int().nonnegative().nullable(),
  waiting_ms: z.number().int().nonnegative(),
});

export const TurnCardSchema = TurnFieldsSchema.extend({
  turn_index: z.number().int().positive(),
  opener_message_id: z.string().nullable(),
  opener_entry_id: z.string().nullable(),
  parent_turn_id: z.string().nullable(),
  stats: TurnStatsSchema,
});

export const TurnDetailSchema = z.object({
  turn: TurnCardSchema,
  nodes: z.array(ActionNodeSchema),
});

export type TurnListView = z.infer<typeof TurnListSchema>;
export type TurnStatsView = z.infer<typeof TurnStatsSchema>;
export type TurnCardView = z.infer<typeof TurnCardSchema>;
export type TurnDetailView = z.infer<typeof TurnDetailSchema>;

export const HitlRequestListViewSchema = z.object({
  agent_id: z.string(),
  items: z.array(
    z.object({
      promise_id: z.string(),
      turn_id: z.string(),
      delivery_id: z.string().nullable(),
      ability_name: z.string(),
      tool_call: z.record(z.unknown()),
      reason: z.string(),
      options: z.record(z.unknown()),
      status: z.string(),
      created_at: z.string(),
      deadline_at: z.string().nullable(),
      resolved_at: z.string().nullable(),
      resolved_by: z.string().nullable(),
      resolution: z.unknown(),
      shell_remember: ShellRememberOptionsSchema.nullable().optional(),
    }),
  ),
});

export type HitlRequestListView = z.infer<typeof HitlRequestListViewSchema>;
