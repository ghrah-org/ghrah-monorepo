// SPDX-FileCopyrightText: 2026 chenxya <chenxya@ghrah.org>
//
// SPDX-License-Identifier: Apache-2.0

export {
  createAbilityTreeClient,
  createRestAbilityTreeTransport,
  type AbilityTreeClient,
  type AbilityTreeTransport,
} from "./ability-tree.js";
export {
  abilityControlClient,
  setAbilityControlClientFactory,
  type AbilityControlClientFactory,
} from "./ability-client.js";
export {
  AbilityConflictError,
  createAbilityControlClient,
  type AbilityAdminQuery,
  type AbilityChangesQuery,
  type AbilityControlClient,
  type AbilityControlClientOptions,
} from "./ability-control.js";
export {
  deriveHttpBase,
  HttpClient,
  HttpError,
  type HttpClientOptions,
  type HttpQuery,
  type HttpQueryValue,
  type HttpRequestOptions,
} from "./http.js";
export { type ConnectStoresOptions, connectStores } from "./bind.js";
export { clearAgentDerived, clearProjectDerived, clearRoomDerived } from "./clear-derived.js";
export {
  type CreateProjectOptions,
  type ListProjectsOptions,
  ObserverClient,
  type ObserverRequestContext,
  type ProjectDeleteTarget,
  type ProjectLifecycleTarget,
  type RoomLifecycleTarget,
} from "./client.js";
export {
  createFrameBatcher,
  createSyncBatcher,
  type FrameBatcher,
  type ScheduleFn,
} from "./frame-batcher.js";
export {
  type ChatEntry,
  type ChatEntryKind,
  contentBlocksFromData,
  type FileChange,
  type FileChangeScope,
  projectNodeToChatEntries,
  projectNodeToFileChanges,
  type RoomLogProjectionScope,
  rebuildChatEntriesFromChain,
  roomLogToChatEntries,
  textFromBlocks,
  withTextContent,
} from "./projection.js";
export {
  type AgentKey,
  type AgentTarget,
  agentKey,
  branchKey,
  type ChainTarget,
  chainKey,
  hasCompleteAgentKey,
  hasCompleteChainTarget,
  type RoomTarget,
  type SessionTarget,
  sameAgent,
  sessionKey,
} from "./scope.js";
export {
  type ActionChainProjection,
  type ProjectionDiagnostic,
  useActionChainsStore,
} from "./stores/action-chains.js";
export {
  abilityAdminKey,
  type AbilityAdminRecord,
  type AbilityFocus,
  grantOperationKeys,
  replacementGrant,
  useAbilityAdminStore,
} from "./stores/ability-admin.js";
export { useAbilityPackagesStore } from "./stores/ability-packages.js";
export { type AgentInfo, type AgentListItem, useAgentsStore } from "./stores/agents.js";
export { type BranchProjection, useBranchesStore } from "./stores/branches.js";
export { useChangesStore } from "./stores/changes.js";
export { DEFAULT_HUMAN_AUTHOR, useChatStore } from "./stores/chat.js";
export { type ConnectionState, useConnectionStore } from "./stores/connection.js";
export {
  type ContextUsageDisplay,
  type ContextUsageEntry,
  type ContextUsageSnapshot,
  useContextUsageStore,
} from "./stores/context-usage.js";
export { type HitlRequest, useHitlStore } from "./stores/hitl.js";
export {
  type AbilityManifestInfo,
  type AgentManifestInfo,
  extractAbilityList,
  extractAgentList,
  extractManifestEntry,
  extractValidationResult,
  useManifestsStore,
  type ValidationResult,
} from "./stores/manifests.js";
export { useProjectsStore } from "./stores/projects.js";
export { usePluginsStore } from "./stores/plugins.js";
export { MAX_CACHED_ROOM_LOGS, useRoomsStore } from "./stores/rooms.js";
export { type SessionProjection, useSessionsStore } from "./stores/sessions.js";
export { useTasksStore } from "./stores/tasks.js";
export { getVisibleNodes } from "./visible-nodes.js";

export { AgentEntity } from "./agent-entity.js";
