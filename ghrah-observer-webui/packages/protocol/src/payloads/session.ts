// SPDX-FileCopyrightText: 2026 chenxya <chenxya@ghrah.org>
//
// SPDX-License-Identifier: Apache-2.0

import { z } from "zod";

// ─── Agent-scoped Session / Chain Payloads ───

export const SessionCreatePayloadSchema = z.object({
  project_id: z.string(),
  agent_id: z.string(),
  agent_name: z.string(),
  name: z.string().nullable().optional(),
  origin_session_id: z.string().nullable().optional(),
  origin_node_id: z.string().nullable().optional(),
  system_prompt: z.string().nullable().optional(),
  metadata: z.record(z.unknown()).optional().default({}),
});

export const SessionActivatePayloadSchema = z.object({
  project_id: z.string(),
  agent_id: z.string(),
  agent_name: z.string(),
  session_id: z.string(),
});

export const SessionListPayloadSchema = z.object({
  project_id: z.string(),
  agent_id: z.string(),
  agent_name: z.string(),
});

export const SessionArchivePayloadSchema = SessionActivatePayloadSchema;
export const SessionDeletePayloadSchema = SessionActivatePayloadSchema;

export const BranchCreatePayloadSchema = z.object({
  project_id: z.string(),
  agent_id: z.string(),
  agent_name: z.string(),
  session_id: z.string(),
  name: z.string(),
  from_node_id: z.string().nullable().optional(),
  parent_branch_id: z.string().nullable().optional(),
  metadata: z.record(z.unknown()).optional().default({}),
});

export const BranchActivatePayloadSchema = z.object({
  project_id: z.string(),
  agent_id: z.string(),
  agent_name: z.string(),
  session_id: z.string(),
  branch_id: z.string(),
});

export const BranchListPayloadSchema = BranchActivatePayloadSchema.omit({ branch_id: true });
export const BranchArchivePayloadSchema = BranchActivatePayloadSchema;
export const BranchDeletePayloadSchema = BranchActivatePayloadSchema;

export const GetChainHistoryPayloadSchema = z.object({
  project_id: z.string(),
  agent_id: z.string(),
  agent_name: z.string(),
  session_id: z.string(),
  branch_id: z.string(),
  limit: z.number().int().optional().default(-1),
});

export const ChainHistoryResultPayloadSchema = z.object({
  project_id: z.string(),
  agent_id: z.string(),
  agent_name: z.string(),
  session_id: z.string(),
  branch_id: z.string(),
  active_session_id: z.string().optional().default(""),
  nodes: z.array(z.record(z.unknown())).optional().default([]),
});

export const SessionInfoPayloadSchema = z.object({
  project_id: z.string().optional().default(""),
  agent_id: z.string().optional().default(""),
  session_id: z.string(),
  agent_name: z.string(),
  name: z.string().optional().default("Session 1"),
  root_node_id: z.string(),
  active_branch_id: z.string(),
  lifecycle: z.string().optional().default("open"),
  system_prompt: z.string().optional().default(""),
  origin_agent_name: z.string().nullable().optional(),
  origin_session_id: z.string().nullable().optional(),
  origin_node_id: z.string().nullable().optional(),
  created_at: z.string().optional().default(""),
  metadata: z.record(z.unknown()).optional().default({}),
  message_count: z.number().int().optional().default(0),
  iteration_count: z.number().int().optional().default(0),
});

export const SessionCreatedPayloadSchema = z.object({
  project_id: z.string().optional().default(""),
  agent_id: z.string().optional().default(""),
  agent_name: z.string(),
  session: SessionInfoPayloadSchema,
});

export const SessionActivatedPayloadSchema = SessionCreatedPayloadSchema;

export const SessionArchivedPayloadSchema = z.object({
  project_id: z.string().optional().default(""),
  agent_id: z.string().optional().default(""),
  agent_name: z.string(),
  session_id: z.string(),
});

export const SessionDeletedPayloadSchema = SessionArchivedPayloadSchema;

export const SessionListResultPayloadSchema = z.object({
  project_id: z.string().optional().default(""),
  agent_id: z.string().optional().default(""),
  agent_name: z.string(),
  active_session_id: z.string().optional().default(""),
  sessions: z.array(SessionInfoPayloadSchema),
});

export const BranchInfoPayloadSchema = z.object({
  branch_id: z.string(),
  session_id: z.string(),
  name: z.string(),
  head_node_id: z.string(),
  lifecycle: z.string().optional().default("open"),
  parent_branch_id: z.string().nullable().optional(),
  fork_point_node_id: z.string().nullable().optional(),
  created_at: z.string().optional().default(""),
  metadata: z.record(z.unknown()).optional().default({}),
});

export const BranchEventPayloadSchema = z.object({
  project_id: z.string().optional().default(""),
  agent_id: z.string().optional().default(""),
  agent_name: z.string(),
  branch: BranchInfoPayloadSchema,
});

export const BranchLifecyclePayloadSchema = z.object({
  project_id: z.string().optional().default(""),
  agent_id: z.string().optional().default(""),
  agent_name: z.string(),
  session_id: z.string(),
  branch_id: z.string(),
});

export const BranchListResultPayloadSchema = z.object({
  project_id: z.string().optional().default(""),
  agent_id: z.string().optional().default(""),
  agent_name: z.string(),
  session_id: z.string(),
  active_branch_id: z.string().optional().default(""),
  branches: z.array(BranchInfoPayloadSchema),
});

export type SessionCreatePayload = z.infer<typeof SessionCreatePayloadSchema>;
export type SessionActivatePayload = z.infer<typeof SessionActivatePayloadSchema>;
export type SessionListPayload = z.infer<typeof SessionListPayloadSchema>;
export type SessionArchivePayload = z.infer<typeof SessionArchivePayloadSchema>;
export type SessionDeletePayload = z.infer<typeof SessionDeletePayloadSchema>;
export type GetChainHistoryPayload = z.infer<typeof GetChainHistoryPayloadSchema>;
export type ChainHistoryResultPayload = z.infer<typeof ChainHistoryResultPayloadSchema>;
export type SessionInfoPayload = z.infer<typeof SessionInfoPayloadSchema>;
export type SessionCreatedPayload = z.infer<typeof SessionCreatedPayloadSchema>;
export type SessionActivatedPayload = z.infer<typeof SessionActivatedPayloadSchema>;
export type SessionArchivedPayload = z.infer<typeof SessionArchivedPayloadSchema>;
export type SessionDeletedPayload = z.infer<typeof SessionDeletedPayloadSchema>;
export type SessionListResultPayload = z.infer<typeof SessionListResultPayloadSchema>;
export type BranchCreatePayload = z.infer<typeof BranchCreatePayloadSchema>;
export type BranchActivatePayload = z.infer<typeof BranchActivatePayloadSchema>;
export type BranchListPayload = z.infer<typeof BranchListPayloadSchema>;
export type BranchArchivePayload = z.infer<typeof BranchArchivePayloadSchema>;
export type BranchDeletePayload = z.infer<typeof BranchDeletePayloadSchema>;
export type BranchInfoPayload = z.infer<typeof BranchInfoPayloadSchema>;
export type BranchEventPayload = z.infer<typeof BranchEventPayloadSchema>;
export type BranchLifecyclePayload = z.infer<typeof BranchLifecyclePayloadSchema>;
export type BranchListResultPayload = z.infer<typeof BranchListResultPayloadSchema>;
