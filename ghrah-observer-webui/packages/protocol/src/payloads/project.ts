// SPDX-FileCopyrightText: 2026 chenxya <chenxya@ghrah.org>
//
// SPDX-License-Identifier: Apache-2.0

import { z } from "zod";

// ─── Project Payloads ───

export const ProjectStatusSchema = z.enum(["active", "paused", "stopped", "failed"]);

export const RecoveryActionSchema = z.enum(["resume", "pause", "drop"]);

export const PathGrantSchema = z.object({
  workspace_id: z.string(),
  subpath: z.string().optional().default("."),
});

export const WorkspaceMountSchema = z.object({
  workspace_id: z.string(),
  role: z.string().nullable().optional(),
  default_for_agents: z.boolean().optional().default(false),
});

export const WritableWorkspaceSpecSchema = z.object({
  locator: z.string(),
  name: z.string().optional().default(""),
  role: z.string().nullable().optional(),
  default_for_agents: z.boolean().optional().default(false),
});

export const AgentPresentationSchema = z
  .object({
    title: z.string().optional().default(""),
    description: z.string().optional().default(""),
    tags: z.array(z.string()).optional().default([]),
    icon: z.string().optional().default(""),
    deprecated: z.boolean().optional().default(false),
  })
  .strict();

export const AgentOriginSchema = z
  .object({
    template_id: z.string(),
    revision: z.number().int().min(1),
  })
  .strict();

/**
 * 项目内一个 Agent 的记录：身份 + 展示 + 编排状态 + 来源。
 *
 * 不承载配置：模型/提示词/上下文/能力的权威在 Core 的实例快照，
 * 模板只在创建时填一次。
 */
export const AgentRecordSchema = z
  .object({
    agent_id: z.string().optional().default(""),
    project_id: z.string().optional().default(""),
    name: z.string(),
    presentation: AgentPresentationSchema.optional().default({}),
    origin: AgentOriginSchema.nullable().optional().default(null),
    desired_state: z.enum(["running", "stopped"]).optional().default("running"),
    runtime_status: z
      .enum(["pending", "running", "stopped", "failed", "lost"])
      .optional()
      .default("pending"),
    last_error: z.string().optional().default(""),
    created_at: z.string().optional().default(""),
    updated_at: z.string().optional().default(""),
  })
  .strict();

/** 按模板创建 Agent 的请求体（零配置）。 */
export const AgentCreateSpecSchema = z
  .object({
    name: z.string().min(1),
    template_id: z.string().min(1),
    revision: z.number().int().min(1),
    desired_state: z.enum(["running", "stopped"]).optional().default("running"),
  })
  .strict();

export const IsolationSpecPayloadSchema = z.object({
  agent_path_grants: z.record(z.array(PathGrantSchema)).optional().default({}),
  agent_private_dir: z.boolean().optional().default(true),
  effect_allowlist: z.array(z.string()).nullable().optional(),
  hitl_override: z.record(z.unknown()).nullable().optional(),
  task_scope: z.boolean().optional().default(true),
});

export const ProjectInfoPayloadSchema = z.object({
  project_id: z.string(),
  name: z.string(),
  description: z.string().optional().default(""),
  project_root_locator: z.string().optional().default(""),
  shell_cwd: z.string().optional().default(""),
  manifest_ref: z.string().optional().default(""),
  workspaces: z.array(WorkspaceMountSchema).optional().default([]),
  agents: z.array(AgentRecordSchema).optional().default([]),
  task_ids: z.array(z.string()).optional().default([]),
  isolation: IsolationSpecPayloadSchema.optional().default({}),
  status: ProjectStatusSchema.optional().default("active"),
  recovery: RecoveryActionSchema.optional().default("resume"),
  version: z.number().int().optional().default(1),
  created_at: z.string().optional().default(""),
  updated_at: z.string().optional().default(""),
  archived_at: z.string().nullable().optional().default(null),
  /** @deprecated A7 旧软删记录迁移专用。 */
  deleted_at: z.string().nullable().optional().default(null),
});

export const ProjectCreatePayloadSchema = z
  .object({
    name: z.string(),
    description: z.string().optional().default(""),
    project_root_locator: z.string().optional().default(""),
    writable_workspaces: z.array(WritableWorkspaceSpecSchema).optional().default([]),
    manifest_ref: z.string().optional().default(""),
    recovery: RecoveryActionSchema.optional().default("resume"),
    shell_cwd: z.string().optional().default(""),
  })
  .strict();

export const ProjectUpdatePayloadSchema = z
  .object({
    project_id: z.string(),
    name: z.string().nullable().optional(),
    description: z.string().nullable().optional(),
    manifest_ref: z.string().nullable().optional(),
    expected_version: z.number().int().nullable().optional(),
    shell_cwd: z.string().nullable().optional(),
  })
  .strict();

export const ProjectIdPayloadSchema = z.object({
  project_id: z.string(),
});

export const ProjectLifecyclePayloadSchema = z.object({
  project_id: z.string(),
  expected_version: z.number().int(),
});

export const ProjectDeletePayloadSchema = ProjectLifecyclePayloadSchema.extend({
  cascade_rooms: z.boolean().optional().default(false),
});

export const ProjectListPayloadSchema = z.object({
  status: ProjectStatusSchema.nullable().optional(),
  archived: z.boolean().nullable().optional().default(false),
  /** @deprecated A7 旧 deleted_at 记录迁移专用。 */
  include_deleted: z.boolean().optional().default(false),
});

export const ProjectAddAgentPayloadSchema = z.object({
  project_id: z.string(),
  agent: AgentCreateSpecSchema,
  expected_version: z.number().int().nullable().optional(),
});

export const ProjectRemoveAgentPayloadSchema = z.object({
  project_id: z.string(),
  agent_name: z.string(),
  agent_id: z.string(),
  expected_version: z.number().int().nullable().optional(),
});

export const ProjectLinkTaskPayloadSchema = z.object({
  project_id: z.string(),
  task_id: z.string(),
  expected_version: z.number().int().nullable().optional(),
});

export const ProjectUnlinkTaskPayloadSchema = z.object({
  project_id: z.string(),
  task_id: z.string(),
  expected_version: z.number().int().nullable().optional(),
});

export const ProjectSetRecoveryPayloadSchema = z.object({
  project_id: z.string(),
  recovery: RecoveryActionSchema.optional().default("resume"),
  expected_version: z.number().int().nullable().optional(),
});

export const ProjectListResultPayloadSchema = z.object({
  projects: z.array(ProjectInfoPayloadSchema).optional().default([]),
  count: z.number().int().optional().default(0),
});

// ─── Project REST 请求/响应（观察端管理面，非 WS wire） ───

export const ProjectUpdateRequestSchema = z
  .object({
    name: z.string().nullable().optional(),
    description: z.string().nullable().optional(),
    manifest_ref: z.string().nullable().optional(),
    shell_cwd: z.string().nullable().optional(),
    expected_version: z.number().int().min(1),
  })
  .strict()
  .refine(
    (value) =>
      [value.name, value.description, value.manifest_ref, value.shell_cwd].some(
        (item) => item !== undefined && item !== null,
      ),
    { message: "至少需要一个可改字段" },
  );

export const ProjectLifecycleRequestSchema = z
  .object({ expected_version: z.number().int().min(1) })
  .strict();

export const ProjectTransitionRequestSchema = z
  .object({ expected_version: z.number().int().min(1).nullable().optional() })
  .strict();

export const ProjectTransitionPayloadSchema = ProjectIdPayloadSchema.extend({
  expected_version: z.number().int().min(1).nullable().optional(),
}).strict();

export const ProjectDeletedViewSchema = z
  .object({
    project_id: z.string(),
    deleted: z.boolean(),
    storage_purged: z.boolean(),
    rooms_deleted: z.number().int().min(0),
  })
  .strict();

// ─── Project Event Payloads ───

export const ProjectEventPayloadSchema = z.object({
  project: ProjectInfoPayloadSchema,
});

export const ProjectAgentEventPayloadSchema = z.object({
  project: ProjectInfoPayloadSchema,
  agent_name: z.string(),
  agent_id: z.string().optional().default(""),
});

export type ProjectStatus = z.infer<typeof ProjectStatusSchema>;
export type RecoveryAction = z.infer<typeof RecoveryActionSchema>;
export type PathGrant = z.infer<typeof PathGrantSchema>;
export type WorkspaceMount = z.infer<typeof WorkspaceMountSchema>;
export type WritableWorkspaceSpec = z.infer<typeof WritableWorkspaceSpecSchema>;
export type AgentRecord = z.infer<typeof AgentRecordSchema>;
export type AgentPresentation = z.infer<typeof AgentPresentationSchema>;
export type AgentOrigin = z.infer<typeof AgentOriginSchema>;
export type AgentCreateSpec = z.infer<typeof AgentCreateSpecSchema>;
export type IsolationSpecPayload = z.infer<typeof IsolationSpecPayloadSchema>;
export type ProjectInfoPayload = z.infer<typeof ProjectInfoPayloadSchema>;
export type ProjectCreatePayload = z.infer<typeof ProjectCreatePayloadSchema>;
export type ProjectUpdatePayload = z.infer<typeof ProjectUpdatePayloadSchema>;
export type ProjectIdPayload = z.infer<typeof ProjectIdPayloadSchema>;
export type ProjectLifecyclePayload = z.infer<typeof ProjectLifecyclePayloadSchema>;
export type ProjectDeletePayload = z.infer<typeof ProjectDeletePayloadSchema>;
export type ProjectListPayload = z.infer<typeof ProjectListPayloadSchema>;
export type ProjectAddAgentPayload = z.infer<typeof ProjectAddAgentPayloadSchema>;
export type ProjectRemoveAgentPayload = z.infer<typeof ProjectRemoveAgentPayloadSchema>;
export type ProjectLinkTaskPayload = z.infer<typeof ProjectLinkTaskPayloadSchema>;
export type ProjectUnlinkTaskPayload = z.infer<typeof ProjectUnlinkTaskPayloadSchema>;
export type ProjectSetRecoveryPayload = z.infer<typeof ProjectSetRecoveryPayloadSchema>;
export type ProjectListResultPayload = z.infer<typeof ProjectListResultPayloadSchema>;
export type ProjectUpdateRequest = z.infer<typeof ProjectUpdateRequestSchema>;
export type ProjectLifecycleRequest = z.infer<typeof ProjectLifecycleRequestSchema>;
export type ProjectTransitionRequest = z.infer<typeof ProjectTransitionRequestSchema>;
export type ProjectTransitionPayload = z.infer<typeof ProjectTransitionPayloadSchema>;
export type ProjectDeletedView = z.infer<typeof ProjectDeletedViewSchema>;
export type ProjectEventPayload = z.infer<typeof ProjectEventPayloadSchema>;
export type ProjectAgentEventPayload = z.infer<typeof ProjectAgentEventPayloadSchema>;
