// SPDX-FileCopyrightText: 2026 chenxya <chenxya@ghrah.org>
//
// SPDX-License-Identifier: Apache-2.0

import { z } from "zod";

export const DeliveryAcceptanceViewSchema = z.object({
  delivery_id: z.string(),
  policy: z.enum(["inject", "queue"]),
  policy_applied: z.boolean(),
  turn_id: z.string().nullable().optional().default(null),
  display_state: z.string(),
  queue_position: z.number().int().optional().default(0),
});

export const MessageAcceptanceViewSchema = z.object({
  message_id: z.string(),
  accepted_at: z.number(),
  deliveries: z.array(DeliveryAcceptanceViewSchema).optional().default([]),
});

export const DeliveryQueueEntryViewSchema = z.object({
  delivery_id: z.string(),
  message_id: z.string(),
  display_state: z.string(),
  position: z.number().int().optional().default(0),
  queue_reason: z.string().nullable().optional().default(null),
  blocked_by_delivery_id: z.string().nullable().optional().default(null),
  turn_id: z.string().nullable().optional().default(null),
  cancelable: z.boolean().optional().default(false),
});

export const DeliveryQueueViewSchema = z.object({
  agent_id: z.string(),
  session_id: z.string().nullable(),
  entries: z.array(DeliveryQueueEntryViewSchema).optional().default([]),
  open_turn_id: z.string().nullable().optional().default(null),
});

export type DeliveryAcceptanceView = z.infer<typeof DeliveryAcceptanceViewSchema>;
export type MessageAcceptanceView = z.infer<typeof MessageAcceptanceViewSchema>;
export type DeliveryQueueEntryView = z.infer<typeof DeliveryQueueEntryViewSchema>;
export type DeliveryQueueView = z.infer<typeof DeliveryQueueViewSchema>;

export const DeliveryViewSchema = z.object({
  seq: z.number().int().optional().default(0),
  delivery_id: z.string(),
  message_id: z.string().optional().default(""),
  recipient_id: z.string().optional().default(""),
  state: z.string().optional().default(""),
  attempts: z.number().int().optional().default(0),
  error: z.string().nullable().optional().default(null),
  consumed_node_id: z.string().nullable().optional().default(null),
  consumed_session_id: z.string().nullable().optional().default(null),
  target_session_id: z.string().nullable().optional().default(null),
  blocked_reason: z.string().nullable().optional().default(null),
  last_node_id: z.string().nullable().optional().default(null),
  resume_requested: z.boolean().optional().default(false),
  turn_id: z.string().nullable().optional().default(null),
  delivery_policy: z.string().optional().default("queue"),
  parked: z.boolean().optional().default(false),
  parked_reason: z.string().nullable().optional().default(null),
  display_state: z.string().optional().default(""),
  result_ready: z.boolean().optional().default(false),
  queue_position: z.number().int().optional().default(0),
  queue_reason: z.string().nullable().optional().default(null),
  blocked_by_delivery_id: z.string().nullable().optional().default(null),
  cancelable: z.boolean().optional().default(false),
});

export type DeliveryView = z.infer<typeof DeliveryViewSchema>;
