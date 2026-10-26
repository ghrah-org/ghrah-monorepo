/** Scheme 描述格式与 Python ghrah.protocol.scheme 对齐；业务 schema 由作者模型导出。 */
import { z } from "zod";

export const ActionDescriptorSchema = z
  .object({
    name: z.string(),
    input: z.record(z.unknown()),
    output: z.record(z.unknown()),
    mutating: z.boolean(),
    cancellable: z.boolean(),
    description: z.string(),
  })
  .strict();
export type ActionDescriptor = z.infer<typeof ActionDescriptorSchema>;

export const ContractDescriptorSchema = z
  .object({
    format: z.literal("ghrah.scheme.local-contract.v1").default("ghrah.scheme.local-contract.v1"),
    kind: z.string(),
    contract: z.string(),
    version: z.string(),
    actions: z.array(ActionDescriptorSchema),
  })
  .strict()
  .refine((value) => new Set(value.actions.map((a) => a.name)).size === value.actions.length, {
    message: "Contract actions must be unique",
  });
export type ContractDescriptor = z.infer<typeof ContractDescriptorSchema>;

export const DiscoveredActionSchema = z
  .object({
    name: z.string(),
    description: z.string(),
    mutating: z.boolean(),
    cancellable: z.boolean(),
    invokable: z.boolean(),
    input_schema: z.record(z.unknown()).nullable(),
    output_schema: z.record(z.unknown()).nullable(),
  })
  .strict();
export type DiscoveredAction = z.infer<typeof DiscoveredActionSchema>;

export const DiscoveredResourceSchema = z
  .object({
    kind: z.string(),
    alias: z.string(),
    contract: z.string(),
    contract_version: z.string(),
    contract_digest: z.string(),
    binding_revision: z.number().int(),
    available: z.boolean(),
    actions: z.array(DiscoveredActionSchema),
  })
  .strict();
export type DiscoveredResource = z.infer<typeof DiscoveredResourceSchema>;

export const CatalogSnapshotSchema = z
  .object({
    format: z.literal("ghrah.scheme.catalog.v1").default("ghrah.scheme.catalog.v1"),
    entries: z.array(DiscoveredResourceSchema),
  })
  .strict();
export type CatalogSnapshot = z.infer<typeof CatalogSnapshotSchema>;
