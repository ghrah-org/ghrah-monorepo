// SPDX-FileCopyrightText: 2026 chenxya <chenxya@ghrah.org>
//
// SPDX-License-Identifier: Apache-2.0

export { ImportMapSchema, parseImportMap, type ImportMap } from "./import-map.js";
export {
  HOST_CAPABILITIES,
  hasHostCapability,
  type HostCapabilities,
  parseManifest,
  type PluginsManifest,
  PluginsManifestSchema,
} from "./manifest.js";
export {
  loadPlugin,
  type LoadPluginOptions,
  type PluginHostApi,
  type PluginLoadFailure,
  type PluginLoadResult,
  type PluginLoadSuccess,
} from "./loader.js";
export {
  applyNegotiationResult,
  buildTsHalfReports,
  type NegotiationOutcome,
} from "./negotiator.js";
export { PluginRegistry, type PluginRegistryState } from "./registry.js";
export { toWireReport, TsHalfSpecSchema, type TsHalfSpec } from "./spec.js";
