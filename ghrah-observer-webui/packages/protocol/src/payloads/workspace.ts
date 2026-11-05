// SPDX-FileCopyrightText: 2026 chenxya <chenxya@ghrah.org>
//
// SPDX-License-Identifier: Apache-2.0

import { z } from "zod";

export const CreateWorkspacePayloadSchema = z.object({
  project_id: z.string(),
  agent_id: z.string(),
  agent_name: z.string(),
});

export const DestroyWorkspacePayloadSchema = z.object({
  project_id: z.string(),
  agent_id: z.string(),
  agent_name: z.string(),
});

export const WorkspaceSnapshotPayloadSchema = z.object({
  project_id: z.string(),
  agent_id: z.string(),
  agent_name: z.string(),
  message: z.string().optional().default(""),
});

export const WorkspaceRollbackPayloadSchema = z.object({
  project_id: z.string(),
  agent_id: z.string(),
  agent_name: z.string(),
  snapshot_id: z.string(),
});

export const WorkspaceDiffPayloadSchema = z.object({
  project_id: z.string(),
  agent_id: z.string(),
  agent_name: z.string(),
  snapshot_id: z.string().nullable().optional(),
});

export const WorkspaceStatusPayloadSchema = z.object({
  project_id: z.string(),
  agent_id: z.string(),
  agent_name: z.string(),
});

export const WorkspaceRegisterPayloadSchema = z.object({
  locator: z.string(),
  name: z.string().optional().default(""),
  provider_type: z.string().nullable().optional(),
});

export const WorkspaceGetPayloadSchema = z.object({
  workspace_id: z.string(),
});

export const WorkspaceListPayloadSchema = z.object({
  provider_type: z.string().nullable().optional(),
});

export const WorkspaceCreatedPayloadSchema = z.object({
  project_id: z.string().optional().default(""),
  agent_id: z.string().optional().default(""),
  cluster_id: z.string().optional().default(""),
  agent_name: z.string(),
  path: z.string(),
});

export const WorkspaceDestroyedPayloadSchema = z.object({
  project_id: z.string().optional().default(""),
  agent_id: z.string().optional().default(""),
  cluster_id: z.string().optional().default(""),
  agent_name: z.string(),
});

export const WorkspaceSnapshotCreatedPayloadSchema = z.object({
  project_id: z.string().optional().default(""),
  agent_id: z.string().optional().default(""),
  cluster_id: z.string().optional().default(""),
  agent_name: z.string(),
  snapshot_id: z.string(),
  message: z.string().optional().default(""),
});

export const WorkspaceRolledBackPayloadSchema = z.object({
  project_id: z.string().optional().default(""),
  agent_id: z.string().optional().default(""),
  cluster_id: z.string().optional().default(""),
  agent_name: z.string(),
  snapshot_id: z.string(),
});

export type CreateWorkspacePayload = z.infer<typeof CreateWorkspacePayloadSchema>;
export type DestroyWorkspacePayload = z.infer<typeof DestroyWorkspacePayloadSchema>;
export type WorkspaceSnapshotPayload = z.infer<typeof WorkspaceSnapshotPayloadSchema>;
export type WorkspaceRollbackPayload = z.infer<typeof WorkspaceRollbackPayloadSchema>;
export type WorkspaceDiffPayload = z.infer<typeof WorkspaceDiffPayloadSchema>;
export type WorkspaceStatusPayload = z.infer<typeof WorkspaceStatusPayloadSchema>;
export type WorkspaceRegisterPayload = z.infer<typeof WorkspaceRegisterPayloadSchema>;
export type WorkspaceGetPayload = z.infer<typeof WorkspaceGetPayloadSchema>;
export type WorkspaceListPayload = z.infer<typeof WorkspaceListPayloadSchema>;

export type WorkspaceCreatedPayload = z.infer<typeof WorkspaceCreatedPayloadSchema>;
export type WorkspaceDestroyedPayload = z.infer<typeof WorkspaceDestroyedPayloadSchema>;
export type WorkspaceSnapshotCreatedPayload = z.infer<typeof WorkspaceSnapshotCreatedPayloadSchema>;
export type WorkspaceRolledBackPayload = z.infer<typeof WorkspaceRolledBackPayloadSchema>;
