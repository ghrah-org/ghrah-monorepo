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

export const AgentSpecSchema = z.object({
  agent_config_name: z.string().nullable().optional(),
  agent_id: z.string().optional(),
  name: z.string(),
  cluster_id: z.string(),
  manifest_ref: z.string().optional().default(""),
  instance_manifest_path: z.string().optional().default(""),
  system_prompt: z.string().optional().default(""),
  abilities: z.array(z.string()).nullable().optional(),
  desired_state: z.enum(["running", "stopped"]).optional().default("running"),
  path_grants: z.array(PathGrantSchema).optional().default([]),
  runtime_status: z.string().optional().default("pending"),
  runtime_error: z.string().optional().default(""),
});

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
  manifest_ref: z.string().optional().default(""),
  cluster_ids: z.array(z.string()).optional().default([]),
  workspaces: z.array(WorkspaceMountSchema).optional().default([]),
  agents: z.array(AgentSpecSchema).optional().default([]),
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
  })
  .strict();

export const ProjectUpdatePayloadSchema = z
  .object({
    project_id: z.string(),
    name: z.string().nullable().optional(),
    description: z.string().nullable().optional(),
    manifest_ref: z.string().nullable().optional(),
    expected_version: z.number().int().nullable().optional(),
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
  agent: AgentSpecSchema,
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
export type AgentSpec = z.infer<typeof AgentSpecSchema>;
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
export type ProjectEventPayload = z.infer<typeof ProjectEventPayloadSchema>;
export type ProjectAgentEventPayload = z.infer<typeof ProjectAgentEventPayloadSchema>;
