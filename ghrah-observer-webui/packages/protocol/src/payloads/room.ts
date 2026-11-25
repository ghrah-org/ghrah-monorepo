// SPDX-FileCopyrightText: 2026 chenxya <chenxya@ghrah.org>
//
// SPDX-License-Identifier: Apache-2.0

import { z } from "zod";
import { TurnCardSchema } from "./turn.js";

// ─── Room Payloads ───

export const RoomSubjectTypeSchema = z.enum(["agent", "human"]);

export const RoomStatusSchema = z.enum(["active", "archived"]);

export const RoomMemberSchema = z.object({
  subject: z.string(),
  subject_type: RoomSubjectTypeSchema,
  /** 可读名称；subject 对 agent 成员保存 project-scoped 稳定 ID。 */
  subject_name: z.string().optional(),
  joined_at: z.string().optional().default(""),
});

export const RoomInfoPayloadSchema = z.object({
  room_id: z.string(),
  project_id: z.string(),
  name: z.string(),
  status: RoomStatusSchema.optional().default("active"),
  members: z.array(RoomMemberSchema).optional().default([]),
  seq_watermark: z.number().int().optional().default(0),
  version: z.number().int().optional().default(1),
  created_at: z.string().optional().default(""),
  updated_at: z.string().optional().default(""),
  archived_at: z.string().nullable().optional().default(null),
});

export const RoomLogEntryPayloadSchema = z.object({
  id: z.string(),
  room_id: z.string(),
  seq: z.number().int(),
  author: z.string(),
  author_type: RoomSubjectTypeSchema,
  timestamp: z.number(),
  data: z.record(z.unknown()).optional().default({}),
});

export const RoomCreatePayloadSchema = z.object({
  project_id: z.string(),
  name: z.string(),
});

export const RoomListPayloadSchema = z.object({
  project_id: z.string().nullable().optional(),
  status: RoomStatusSchema.nullable().optional(),
});

export const RoomIdPayloadSchema = z.object({
  room_id: z.string(),
});

export const RoomUpdatePayloadSchema = z.object({
  room_id: z.string(),
  name: z.string().nullable().optional(),
  expected_version: z.number().int().nullable().optional(),
});

export const RoomLifecyclePayloadSchema = z.object({
  room_id: z.string(),
  expected_version: z.number().int(),
});

export const RoomDeletePayloadSchema = RoomLifecyclePayloadSchema;

export const RoomJoinPayloadSchema = z.object({
  room_id: z.string(),
  subject: z.string(),
  subject_type: RoomSubjectTypeSchema,
  subject_name: z.string().optional(),
});

export const RoomLeavePayloadSchema = z.object({
  room_id: z.string(),
  subject: z.string(),
});

export const RoomGetLogPayloadSchema = z.object({
  room_id: z.string(),
  since_seq: z.number().int().nullable().optional(),
  limit: z.number().int().optional().default(100),
});

export const RoomSendPayloadSchema = z.object({
  room_id: z.string(),
  author: z.string(),
  author_type: RoomSubjectTypeSchema,
  data: z.record(z.unknown()).optional().default({}),
  delivery_policy: z.enum(["inject", "queue"]).default("queue"),
});

export const RoomListResultPayloadSchema = z.object({
  rooms: z.array(RoomInfoPayloadSchema).optional().default([]),
  count: z.number().int().optional().default(0),
});

export const RoomLogResultPayloadSchema = z.object({
  entries: z.array(RoomLogEntryPayloadSchema).optional().default([]),
  count: z.number().int().optional().default(0),
});

// ─── Room Event Payloads ───

export const RoomEventPayloadSchema = z.object({
  room: RoomInfoPayloadSchema,
});

export const RoomDeletedEventPayloadSchema = z.object({
  room_id: z.string(),
  project_id: z.string(),
});

export const RoomMemberEventPayloadSchema = z.object({
  room: RoomInfoPayloadSchema,
  member: RoomMemberSchema.nullable().optional(),
  subject: z.string().nullable().optional(),
});

export const RoomLogEventPayloadSchema = z.object({
  entry: RoomLogEntryPayloadSchema,
});

export type RoomSubjectType = z.infer<typeof RoomSubjectTypeSchema>;
export type RoomStatus = z.infer<typeof RoomStatusSchema>;
export type RoomMember = z.infer<typeof RoomMemberSchema>;
export type RoomInfoPayload = z.infer<typeof RoomInfoPayloadSchema>;
export type RoomLogEntryPayload = z.infer<typeof RoomLogEntryPayloadSchema>;
export type RoomCreatePayload = z.infer<typeof RoomCreatePayloadSchema>;
export type RoomListPayload = z.infer<typeof RoomListPayloadSchema>;
export type RoomIdPayload = z.infer<typeof RoomIdPayloadSchema>;
export type RoomUpdatePayload = z.infer<typeof RoomUpdatePayloadSchema>;
export type RoomLifecyclePayload = z.infer<typeof RoomLifecyclePayloadSchema>;
export type RoomDeletePayload = z.infer<typeof RoomDeletePayloadSchema>;
export type RoomJoinPayload = z.infer<typeof RoomJoinPayloadSchema>;
export type RoomLeavePayload = z.infer<typeof RoomLeavePayloadSchema>;
export type RoomGetLogPayload = z.infer<typeof RoomGetLogPayloadSchema>;
export type RoomSendPayload = z.infer<typeof RoomSendPayloadSchema>;
export type RoomListResultPayload = z.infer<typeof RoomListResultPayloadSchema>;
export type RoomLogResultPayload = z.infer<typeof RoomLogResultPayloadSchema>;
export type RoomEventPayload = z.infer<typeof RoomEventPayloadSchema>;
export type RoomDeletedEventPayload = z.infer<typeof RoomDeletedEventPayloadSchema>;
export type RoomMemberEventPayload = z.infer<typeof RoomMemberEventPayloadSchema>;
export type RoomLogEventPayload = z.infer<typeof RoomLogEventPayloadSchema>;

/** Room transcript REST 视图，与 Python observer_room 对齐。 */
export const TranscriptDeliverySchema = z.object({
  delivery_id: z.string(),
  message_id: z.string(),
  recipient_id: z.string(),
  state: z.string(),
  error: z.string().nullable(),
  blocked_reason: z.string().nullable(),
  turn_id: z.string().nullable(),
  delivery_policy: z.string(),
  display_state: z.string(),
  queue_position: z.number().int().nonnegative(),
  queue_reason: z.string().nullable(),
  cancelable: z.boolean(),
  injection_step: z.number().int().positive().nullable(),
});

export const TranscriptEntrySchema = z.object({
  entry_id: z.string(),
  seq: z.number().int(),
  author: z.string(),
  author_type: RoomSubjectTypeSchema,
  timestamp: z.number(),
  message_id: z.string().nullable(),
  content: z.string(),
  content_blocks: z.array(z.record(z.unknown())).nullable(),
  targets: z.array(z.string()),
  deliveries: z.array(TranscriptDeliverySchema),
  turn_id: z.string().nullable(),
  insert_before_turn_id: z.string().nullable(),
  injection_step: z.number().int().positive().nullable(),
  human_state: z.string().nullable(),
  node_id: z.string().nullable(),
});

export const RoomTranscriptSchema = z.object({
  project_id: z.string(),
  room_id: z.string(),
  entries: z.array(TranscriptEntrySchema),
  turns: z.array(TurnCardSchema),
  next_before_seq: z.number().int().nullable(),
  next_since_seq: z.number().int().nullable(),
  latest_seq: z.number().int(),
  has_more: z.boolean(),
  read_at: z.string(),
});

export type TranscriptDeliveryView = z.infer<typeof TranscriptDeliverySchema>;
export type TranscriptEntryView = z.infer<typeof TranscriptEntrySchema>;
export type RoomTranscriptView = z.infer<typeof RoomTranscriptSchema>;
