// SPDX-FileCopyrightText: 2026 chenxya <chenxya@ghrah.org>
//
// SPDX-License-Identifier: Apache-2.0

import { z } from "zod";

// ─── Task Payloads ───

export const TaskStatusSchema = z.enum([
  "pending",
  "in_progress",
  "blocked",
  "delivered",
  "completed",
  "failed",
  "canceled",
]);

export const TaskPrioritySchema = z.enum(["low", "normal", "high", "urgent"]);

export const ClaimantTypeSchema = z.enum(["agent", "human"]);

export const ClaimStateSchema = z.enum(["submitted", "verified", "rejected", "superseded"]);

export const ClaimVerdictSchema = z.enum(["verified", "rejected"]);

export const TaskVerificationPayloadSchema = z.object({
  evidence_min: z.number().int().nullable().optional(),
  evidence_kinds: z.array(z.string()).optional().default([]),
  checks: z.array(z.string()).optional().default([]),
  approver: z.string().nullable().optional(),
});

export const TaskInfoPayloadSchema = z.object({
  task_id: z.string(),
  project_id: z.string(),
  title: z.string(),
  description: z.string().optional().default(""),
  agent_id: z.string().nullable().optional(),
  agent_name: z.string().nullable().optional(),
  status: TaskStatusSchema.optional().default("pending"),
  priority: TaskPrioritySchema.optional().default("normal"),
  parent_id: z.string().nullable().optional(),
  dependencies: z.array(z.string()).optional().default([]),
  result: z.unknown().nullable().optional(),
  error: z.string().nullable().optional(),
  created_at: z.string().optional().default(""),
  updated_at: z.string().optional().default(""),
  started_at: z.string().nullable().optional(),
  completed_at: z.string().nullable().optional(),
  metadata: z.record(z.unknown()).optional().default({}),
  verification: TaskVerificationPayloadSchema.nullable().optional(),
});

export const TaskCreatePayloadSchema = z.object({
  title: z.string(),
  project_id: z.string(),
  description: z.string().optional().default(""),
  agent_id: z.string().nullable().optional(),
  agent_name: z.string().nullable().optional(),
  priority: TaskPrioritySchema.optional().default("normal"),
  parent_id: z.string().nullable().optional(),
  dependencies: z.array(z.string()).optional().default([]),
  metadata: z.record(z.unknown()).optional().default({}),
});

export const TaskUpdatePayloadSchema = z.object({
  task_id: z.string(),
  title: z.string().nullable().optional(),
  description: z.string().nullable().optional(),
  agent_id: z.string().nullable().optional(),
  agent_name: z.string().nullable().optional(),
  status: TaskStatusSchema.nullable().optional(),
  priority: TaskPrioritySchema.nullable().optional(),
  parent_id: z.string().nullable().optional(),
  dependencies: z.array(z.string()).nullable().optional(),
  result: z.unknown().nullable().optional(),
  error: z.string().nullable().optional(),
  metadata: z.record(z.unknown()).nullable().optional(),
  metadata_patch: z.record(z.unknown()).nullable().optional(),
  expected_version: z.number().int().nullable().optional(),
});

export const TaskIdPayloadSchema = z.object({
  task_id: z.string(),
});

export const TaskAssignPayloadSchema = z.object({
  task_id: z.string(),
  agent_id: z.string().optional().default(""),
  agent_name: z.string(),
});

export const TaskCompletePayloadSchema = z.object({
  task_id: z.string(),
  result: z.unknown().nullable().optional(),
});

export const TaskFailPayloadSchema = z.object({
  task_id: z.string(),
  error: z.string(),
});

export const TaskCancelPayloadSchema = z.object({
  task_id: z.string(),
  reason: z.string().nullable().optional(),
});

export const TaskBlockPayloadSchema = z.object({
  task_id: z.string(),
  reason: z.string().nullable().optional(),
});

export const TaskListPayloadSchema = z.object({
  agent_id: z.string().nullable().optional(),
  agent_name: z.string().nullable().optional(),
  status: z
    .union([TaskStatusSchema, z.array(TaskStatusSchema)])
    .nullable()
    .optional(),
  parent_id: z.string().nullable().optional(),
  project_id: z.string().nullable().optional(),
  include_terminal: z.boolean().optional().default(true),
  limit: z.number().int().optional().default(100),
});

export const TaskDeletePayloadSchema = z.object({
  task_id: z.string(),
  force: z.boolean().optional().default(false),
});

export const TaskListResultPayloadSchema = z.object({
  tasks: z.array(TaskInfoPayloadSchema).optional().default([]),
  count: z.number().int().optional().default(0),
});

export const TaskEventPayloadSchema = z.object({
  task: TaskInfoPayloadSchema,
  previous_status: TaskStatusSchema.nullable().optional(),
  reason: z.string().nullable().optional(),
  agent_id: z.string().nullable().optional(),
  agent_name: z.string().nullable().optional(),
});

// ─── Task 归因切面：claims / evidence / verification gaps ───
// evidence.kind 与 checker 名是开放字符串（插件可声明新 evidence_kinds），
// wire 不做闭合枚举——未知类型安全穿透不崩溃。

export const TaskEvidenceInputSchema = z.object({
  kind: z.string(),
  ref: z.string(),
  digest: z.string().nullable().optional(),
  payload: z.record(z.unknown()).optional().default({}),
});

export const TaskEvidencePayloadSchema = z.object({
  evidence_id: z.string(),
  kind: z.string(),
  ref: z.string(),
  digest: z.string().nullable().optional(),
  payload: z.record(z.unknown()).optional().default({}),
  created_by: z.string().nullable().optional(),
  created_at: z.string().optional().default(""),
});

export const TaskCheckOutcomePayloadSchema = z.object({
  checker: z.string(),
  passed: z.boolean(),
  detail: z.string().nullable().optional(),
});

export const TaskProvenancePayloadSchema = z.object({
  agent_id: z.string().nullable().optional(),
  session_id: z.string().nullable().optional(),
  branch_id: z.string().nullable().optional(),
  node_id: z.string().nullable().optional(),
});

export const TaskClaimPayloadSchema = z.object({
  claim_id: z.string(),
  task_id: z.string(),
  claimant_type: ClaimantTypeSchema.optional().default("agent"),
  claimant_id: z.string(),
  claimant_name: z.string().nullable().optional(),
  note: z.string().nullable().optional(),
  evidence: z.array(TaskEvidencePayloadSchema).optional().default([]),
  state: ClaimStateSchema,
  checks: z.array(TaskCheckOutcomePayloadSchema).optional().default([]),
  verdict_by: z.string().nullable().optional(),
  verdict_at: z.string().nullable().optional(),
  verdict_reason: z.string().nullable().optional(),
  provenance: TaskProvenancePayloadSchema.nullable().optional(),
  created_at: z.string().optional().default(""),
});

export const TaskSubmitCompletionPayloadSchema = z.object({
  task_id: z.string(),
  claimant_type: ClaimantTypeSchema.optional().default("agent"),
  claimant_id: z.string(),
  claimant_name: z.string().nullable().optional(),
  note: z.string().nullable().optional(),
  evidence: z.array(TaskEvidenceInputSchema).optional().default([]),
  provenance: TaskProvenancePayloadSchema.nullable().optional(),
  expected_version: z.number().int().nullable().optional(),
});

export const TaskVerifyPayloadSchema = z.object({
  task_id: z.string(),
  claim_id: z.string(),
  verdict: ClaimVerdictSchema,
  verifier_id: z.string(),
  verifier_name: z.string().nullable().optional(),
  reason: z.string().nullable().optional(),
  expected_version: z.number().int().nullable().optional(),
});

export const TaskClaimListPayloadSchema = z.object({
  task_id: z.string().nullable().optional(),
  claimant_id: z.string().nullable().optional(),
  state: ClaimStateSchema.or(z.array(ClaimStateSchema)).nullable().optional(),
  limit: z.number().int().optional().default(100),
});

export const TaskClaimListResultPayloadSchema = z.object({
  claims: z.array(TaskClaimPayloadSchema).optional().default([]),
  count: z.number().int().optional().default(0),
});

export const TaskClaimEventPayloadSchema = z.object({
  task: TaskInfoPayloadSchema,
  claim: TaskClaimPayloadSchema,
  previous_status: TaskStatusSchema.nullable().optional(),
  reason: z.string().nullable().optional(),
});

export const TaskMissingCheckPayloadSchema = z.object({
  checker: z.string(),
  candidates: z.array(z.string()).optional().default([]),
});

export const TaskVerificationGapsPayloadSchema = z.object({
  evidence_have: z.number().int().optional().default(0),
  evidence_need: z.number().int().nullable().optional(),
  missing_evidence_kinds: z.array(z.string()).optional().default([]),
  missing_checks: z.array(TaskMissingCheckPayloadSchema).optional().default([]),
  failed_checks: z.array(TaskCheckOutcomePayloadSchema).optional().default([]),
  missing_approver: z.string().nullable().optional(),
});

export const TaskDumpPayloadSchema = z.object({
  project_id: z.string().nullable().optional(),
  include_deleted: z.boolean().optional().default(false),
  limit: z.number().int().nullable().optional(),
});

export const TaskDumpResultPayloadSchema = z.object({
  tasks: z.array(TaskInfoPayloadSchema).optional().default([]),
  claims: z.array(TaskClaimPayloadSchema).optional().default([]),
  evidence: z.array(TaskEvidencePayloadSchema).optional().default([]),
});

export type TaskStatus = z.infer<typeof TaskStatusSchema>;
export type TaskPriority = z.infer<typeof TaskPrioritySchema>;
export type ClaimantType = z.infer<typeof ClaimantTypeSchema>;
export type ClaimState = z.infer<typeof ClaimStateSchema>;
export type ClaimVerdict = z.infer<typeof ClaimVerdictSchema>;
export type TaskInfoPayload = z.infer<typeof TaskInfoPayloadSchema>;
export type TaskVerificationPayload = z.infer<typeof TaskVerificationPayloadSchema>;
export type TaskCreatePayload = z.infer<typeof TaskCreatePayloadSchema>;
export type TaskUpdatePayload = z.infer<typeof TaskUpdatePayloadSchema>;
export type TaskIdPayload = z.infer<typeof TaskIdPayloadSchema>;
export type TaskAssignPayload = z.infer<typeof TaskAssignPayloadSchema>;
export type TaskCompletePayload = z.infer<typeof TaskCompletePayloadSchema>;
export type TaskFailPayload = z.infer<typeof TaskFailPayloadSchema>;
export type TaskCancelPayload = z.infer<typeof TaskCancelPayloadSchema>;
export type TaskBlockPayload = z.infer<typeof TaskBlockPayloadSchema>;
export type TaskListPayload = z.infer<typeof TaskListPayloadSchema>;
export type TaskDeletePayload = z.infer<typeof TaskDeletePayloadSchema>;
export type TaskListResultPayload = z.infer<typeof TaskListResultPayloadSchema>;
export type TaskEventPayload = z.infer<typeof TaskEventPayloadSchema>;
export type TaskEvidenceInput = z.infer<typeof TaskEvidenceInputSchema>;
export type TaskEvidencePayload = z.infer<typeof TaskEvidencePayloadSchema>;
export type TaskCheckOutcomePayload = z.infer<typeof TaskCheckOutcomePayloadSchema>;
export type TaskProvenancePayload = z.infer<typeof TaskProvenancePayloadSchema>;
export type TaskClaimPayload = z.infer<typeof TaskClaimPayloadSchema>;
export type TaskSubmitCompletionPayload = z.infer<typeof TaskSubmitCompletionPayloadSchema>;
export type TaskVerifyPayload = z.infer<typeof TaskVerifyPayloadSchema>;
export type TaskClaimListPayload = z.infer<typeof TaskClaimListPayloadSchema>;
export type TaskClaimListResultPayload = z.infer<typeof TaskClaimListResultPayloadSchema>;
export type TaskClaimEventPayload = z.infer<typeof TaskClaimEventPayloadSchema>;
export type TaskMissingCheckPayload = z.infer<typeof TaskMissingCheckPayloadSchema>;
export type TaskVerificationGapsPayload = z.infer<typeof TaskVerificationGapsPayloadSchema>;
export type TaskDumpPayload = z.infer<typeof TaskDumpPayloadSchema>;
export type TaskDumpResultPayload = z.infer<typeof TaskDumpResultPayloadSchema>;
