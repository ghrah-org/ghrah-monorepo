/** Ability 关系切片；与 Python ghrah.protocol.ability_tree 和导出样例对齐。 */
import { z } from "zod";

const key = z.string().regex(/^[a-z][a-z0-9_-]{0,63}$/);
export const AbilityTreeIdSchema = z.string().regex(/^[A-Za-z0-9][A-Za-z0-9_-]{0,127}$/);
const revision = z.number().int().nonnegative();
export const OperationNodeSchema = z
  .object({
    key,
    description: z.string(),
    input_schema: z.record(z.unknown()),
    output_schema: z.record(z.unknown()),
  })
  .strict();
export const AbilityNodeSchema = z
  .object({
    alias: key,
    instance_id: AbilityTreeIdSchema,
    instance_revision: revision,
    definition_digest: z.string(),
    version_digest: z.string(),
    revision,
    allowed_roots: z.array(z.string()),
    operations: z
      .array(OperationNodeSchema)
      .refine((items) => new Set(items.map((item) => item.key)).size === items.length, {
        message: "Duplicate Operation key",
      }),
    availability: z.enum(["ready", "unavailable", "unknown"]).default("unknown"),
  })
  .strict();
export const BoundToolNodeSchema = z
  .object({
    name: key,
    alias: key,
    member: key,
    grant_revision: revision,
    revision,
  })
  .strict();
export const RuntimeObservationSchema = z
  .object({
    host_id: AbilityTreeIdSchema,
    observed_at: z.string().datetime(),
  })
  .strict();
export const AbilityTreeViewSchema = z
  .object({
    format: z.literal("ghrah.ability-tree.v1").default("ghrah.ability-tree.v1"),
    project_id: AbilityTreeIdSchema,
    agent_id: AbilityTreeIdSchema,
    run_id: AbilityTreeIdSchema,
    revision,
    abilities: z.array(AbilityNodeSchema),
    bound_tools: z.array(BoundToolNodeSchema),
    runtime: RuntimeObservationSchema.nullable().default(null),
  })
  .strict()
  .refine(
    (value) => new Set(value.abilities.map((item) => item.alias)).size === value.abilities.length,
    { message: "Duplicate Ability alias" },
  )
  .refine(
    (value) =>
      new Set(value.bound_tools.map((item) => item.name)).size === value.bound_tools.length,
    { message: "Duplicate tool name" },
  );
export const GrantAbilityRequestSchema = z
  .object({
    alias: key,
    instance_id: AbilityTreeIdSchema,
    members: z.array(key).min(1),
    allowed_roots: z.array(z.string()).min(1),
    expected_revision: revision,
  })
  .strict();
export const RevokeAbilityRequestSchema = z
  .object({ alias: key, expected_revision: revision })
  .strict();
export const BindToolRequestSchema = z
  .object({
    alias: key,
    member: key,
    tool_name: key,
    expected_revision: revision,
  })
  .strict();
export const UnbindToolRequestSchema = z
  .object({ tool_name: key, expected_revision: revision })
  .strict();
export const TreeChangeResultSchema = z
  .object({
    view: AbilityTreeViewSchema.nullable().default(null),
    error: z
      .enum(["not_found", "access_denied", "conflict", "invalid_change", "unavailable"])
      .nullable()
      .default(null),
  })
  .strict()
  .refine((value) => (value.view === null) !== (value.error === null), {
    message: "Expected exactly one view or error",
  });
export type AbilityTreeView = z.infer<typeof AbilityTreeViewSchema>;
export type BindToolRequest = z.infer<typeof BindToolRequestSchema>;
export type UnbindToolRequest = z.infer<typeof UnbindToolRequestSchema>;
export type TreeChangeResult = z.infer<typeof TreeChangeResultSchema>;
