import {
  type AbilityManifestInfo,
  type AgentManifestInfo,
  type AgentTarget,
  type ChainTarget,
  type CreateProjectOptions,
  connectStores,
  extractAbilityList,
  extractAgentList,
  extractManifestEntry,
  extractValidationResult,
  type ListProjectsOptions,
  ObserverClient,
  type SessionTarget,
  useActionChainsStore,
  useAgentsStore,
  useChangesStore,
  useChatStore,
  useConnectionStore,
  useHitlStore,
  useManifestsStore,
  useProjectsStore,
  useRoomsStore,
} from "@ghrah/observer-core";
import type { AbilityDefinitionPayload, AgentConfigPayload } from "@ghrah/protocol";
import { ref, shallowRef, watch } from "vue";
import { i18n } from "@/i18n";
import { usePlugins } from "@/composables/usePlugins";

const client = shallowRef<ObserverClient | null>(null);
const error = ref<string | null>(null);
let unbind: (() => void) | null = null;

// ── 导航状态持久化（active project/room → localStorage） ──

const NAV_STORAGE_KEY = "ghrah-nav";
let navPersistenceStarted = false;

interface NavState {
  projectId: string | null;
  roomId: string | null;
}

function readNavState(): NavState | null {
  try {
    const raw = localStorage.getItem(NAV_STORAGE_KEY);
    if (!raw) return null;
    const parsed = JSON.parse(raw) as Partial<NavState>;
    return {
      projectId: typeof parsed.projectId === "string" ? parsed.projectId : null,
      roomId: typeof parsed.roomId === "string" ? parsed.roomId : null,
    };
  } catch {
    return null;
  }
}

async function withClient<T>(fn: (c: ObserverClient) => Promise<T>): Promise<T | null> {
  if (!client.value) return null;
  try {
    return await fn(client.value);
  } catch (err) {
    error.value = err instanceof Error ? err.message : String(err);
    return null;
  }
}

export function useObserver() {
  const connection = useConnectionStore();
  const agents = useAgentsStore();
  const actionChains = useActionChainsStore();
  const hitl = useHitlStore();
  const chat = useChatStore();
  const changes = useChangesStore();
  const manifests = useManifestsStore();
  const rooms = useRoomsStore();
  const projects = useProjectsStore();

  // 首次使用时注册持久化 watch（需在 active pinia 语境下调用）。
  if (!navPersistenceStarted && typeof localStorage !== "undefined") {
    navPersistenceStarted = true;
    watch([() => projects.activeProjectId, () => rooms.activeRoomId], ([projectId, roomId]) => {
      try {
        localStorage.setItem(NAV_STORAGE_KEY, JSON.stringify({ projectId, roomId }));
      } catch {
        // 忽略存储失败（如隐私模式）
      }
    });
  }

  async function connect(serverUrl?: string) {
    const url = serverUrl ?? connection.serverUrl;

    if (client.value) {
      unbind?.();
      unbind = null;
      await client.value.disconnect();
      client.value = null;
    }

    connection.setConnecting();
    error.value = null;

    // 插件清单必须先于连接就绪（连接后补装不会重发协商）；失败走空清单。
    const { loadManifest, tsHalfProvider } = usePlugins();
    await loadManifest();

    try {
      const obsClient = new ObserverClient(url);
      unbind = connectStores(obsClient, { tsHalfProvider });

      await obsClient.connect();
      client.value = obsClient;

      // 初始同步完成后恢复持久化的 active project/room。
      const saved = readNavState();
      if (saved?.projectId && projects.projects.has(saved.projectId)) {
        projects.setActiveProject(saved.projectId);
      }
      if (saved?.roomId && rooms.rooms.has(saved.roomId)) {
        await switchRoom(saved.roomId);
      }
    } catch (err) {
      connection.setDisconnected();
      error.value = err instanceof Error ? err.message : String(err);
    }
  }

  async function disconnect() {
    if (client.value) {
      unbind?.();
      unbind = null;
      await client.value.disconnect();
      client.value = null;
    }
    connection.setDisconnected();
  }

  async function autoConnect() {
    return connect(connection.serverUrl);
  }

  async function sendMessage(target: AgentTarget, content: string) {
    return withClient((c) => c.sendMessage(target, content));
  }

  async function sendAgentMessage(
    source: AgentTarget,
    target: AgentTarget,
    content: string,
    fireAndForget: boolean,
  ) {
    return withClient((c) => c.sendAgentMessage(source, target, content, fireAndForget));
  }

  async function getAgentInfo(target: AgentTarget) {
    return withClient((c) => c.getAgentInfo(target));
  }

  async function registerAbility(target: AgentTarget, ability: AbilityDefinitionPayload) {
    return withClient((c) => c.registerAbility(target, ability));
  }

  async function sendHitlResponse(promiseId: string, approved: boolean, reason?: string) {
    const result = await withClient((c) => c.sendHitlResponse(promiseId, approved, reason));
    // 仅在回执成功时移除卡片；失败（promise 过期/送达失败）保留待重试
    if (result !== null && result.success) {
      hitl.removeRequest(promiseId);
    }
  }

  async function spawnAgent(config: AgentConfigPayload, manifestRef?: string | null) {
    const projectId = projects.activeProjectId;
    if (!projectId) return null;
    return withClient((c) => c.spawnAgent(projectId, config, null, manifestRef ?? null));
  }

  async function terminateAgent(target: AgentTarget) {
    return withClient((c) => c.terminateAgent(target));
  }

  async function agentCompactContext(target: AgentTarget) {
    return withClient((c) => c.agentCompactContext(target));
  }

  async function agentReset(target: AgentTarget) {
    return withClient((c) => c.agentReset(target));
  }

  // ── Room / Project / 导航 ──

  /** 人类向 room 发消息（author 默认对齐 DEFAULT_HUMAN_AUTHOR）。 */
  async function roomSend(roomId: string, content: string, targets?: string[]) {
    return withClient((c) =>
      c.roomSend(roomId, {
        message: content,
        ...(targets && targets.length > 0 ? { targets } : {}),
      }),
    );
  }

  async function listRooms(projectId?: string | null, status?: string | null) {
    return withClient((c) => c.listRooms(projectId ?? undefined, status ?? undefined));
  }

  async function createRoom(projectId: string, name: string) {
    return withClient((c) => c.createRoom(projectId, name));
  }

  async function updateRoom(roomId: string, name: string, expectedVersion: number) {
    return withClient((c) => c.updateRoom(roomId, name, expectedVersion));
  }

  async function archiveRoom(roomId: string, expectedVersion: number) {
    return withClient((c) => c.archiveRoom({ roomId, expectedVersion }));
  }

  async function deleteRoom(roomId: string, expectedVersion: number) {
    return withClient((c) => c.deleteRoom({ roomId, expectedVersion }));
  }

  async function restoreRoom(roomId: string, expectedVersion: number) {
    return withClient((c) => c.restoreRoom({ roomId, expectedVersion }));
  }

  async function joinRoom(
    roomId: string,
    subject: string,
    subjectType: "agent" | "human",
    subjectName?: string,
  ) {
    return withClient((c) => c.joinRoom(roomId, subject, subjectType, subjectName));
  }

  async function leaveRoom(roomId: string, subject: string) {
    return withClient((c) => c.leaveRoom(roomId, subject));
  }

  /** 拉取 room 历史；bind 的 ROOM_GET_LOG 处理会把结果灌入 rooms store。 */
  async function getRoomLog(roomId: string) {
    return withClient((c) => c.getRoomLog(roomId));
  }

  async function listProjects(options: ListProjectsOptions = {}) {
    return withClient((c) => c.listProjects(options));
  }

  async function createProject(name: string, options: CreateProjectOptions) {
    return withClient((c) => c.createProject(name, options));
  }

  async function updateProject(
    projectId: string,
    fields: {
      name?: string | null;
      description?: string | null;
      manifestRef?: string | null;
      expectedVersion?: number | null;
    },
  ) {
    return withClient((c) => c.updateProject(projectId, fields));
  }

  async function archiveProject(projectId: string, expectedVersion: number) {
    return withClient((c) => c.archiveProject({ projectId, expectedVersion }));
  }

  async function restoreProject(projectId: string, expectedVersion: number) {
    return withClient((c) => c.restoreProject({ projectId, expectedVersion }));
  }

  async function deleteProject(projectId: string, expectedVersion: number, cascadeRooms = false) {
    return withClient((c) => c.deleteProject({ projectId, expectedVersion, cascadeRooms }));
  }

  async function listAgents(projectId: string) {
    return withClient((c) => c.listAgents(projectId));
  }

  async function listSessions(target: AgentTarget) {
    return withClient((c) => c.listSessions(target));
  }

  async function createSession(target: AgentTarget, options: { originSessionId?: string } = {}) {
    return withClient((c) =>
      c.createSession(target, {
        originSessionId: options.originSessionId,
      }),
    );
  }

  async function activateSession(target: SessionTarget) {
    return withClient((c) => c.activateSession(target));
  }

  async function archiveSession(target: SessionTarget) {
    return withClient((c) => c.archiveSession(target));
  }

  async function deleteSession(target: SessionTarget) {
    return withClient((c) => c.deleteSession(target));
  }

  async function listBranches(target: SessionTarget) {
    return withClient((c) => c.listBranches(target));
  }

  async function createBranch(
    target: SessionTarget,
    name: string,
    options: { fromNodeId?: string; parentBranchId?: string } = {},
  ) {
    return withClient((c) =>
      c.createBranch(target, name, {
        fromNodeId: options.fromNodeId,
        parentBranchId: options.parentBranchId,
      }),
    );
  }

  async function activateBranch(target: ChainTarget) {
    return withClient((c) => c.activateBranch(target));
  }

  async function archiveBranch(target: ChainTarget) {
    return withClient((c) => c.archiveBranch(target));
  }

  async function deleteBranch(target: ChainTarget) {
    return withClient((c) => c.deleteBranch(target));
  }

  /** 切换 active room；缓存缺失时拉历史。 */
  async function switchRoom(roomId: string | null) {
    rooms.setActiveRoom(roomId);
    if (roomId !== null && !rooms.logs.has(roomId)) {
      await getRoomLog(roomId);
    }
  }

  function switchProject(projectId: string | null) {
    projects.setActiveProject(projectId);
  }

  function selectAgent(target: AgentTarget | null) {
    agents.selectAgent(target);
  }

  // ── Manifest: Ability ──

  async function listManifestAbilities(namespace?: string | null) {
    const result = await withClient((c) => c.listManifestAbilities(namespace));
    if (result?.success) {
      const abilities = extractAbilityList(result.data);
      if (abilities) manifests.setAbilities(abilities);
    }
    return result;
  }

  async function getAbility(fullName: string) {
    const result = await withClient((c) => c.getAbility(fullName));
    if (result?.success) {
      const entry = extractManifestEntry(result.data);
      if (entry) manifests.upsertAbility(entry as AbilityManifestInfo);
    }
    return result;
  }

  async function putAbility(fullName: string, content: string, overwrite?: boolean) {
    const result = await withClient((c) => c.putAbility(fullName, content, overwrite));
    if (result?.success) {
      const entry = extractManifestEntry(result.data);
      if (entry) manifests.upsertAbility(entry as AbilityManifestInfo);
    }
    return result;
  }

  async function deleteAbility(fullName: string) {
    const result = await withClient((c) => c.deleteAbility(fullName));
    if (result?.success) manifests.removeAbility(fullName);
    return result;
  }

  // ── Manifest: Agent ──

  async function listManifestAgents(namespace?: string | null) {
    manifests.agentsLoading = true;
    try {
      const result = await withClient((c) => c.listManifestAgents(namespace));
      if (result?.success) {
        const agents = extractAgentList(result.data);
        if (agents) manifests.setAgents(agents);
      }
      return result;
    } finally {
      manifests.agentsLoading = false;
    }
  }

  async function getAgent(fullName: string) {
    const result = await withClient((c) => c.getAgent(fullName));
    if (result?.success) {
      const entry = extractManifestEntry(result.data);
      if (entry) manifests.upsertAgent(entry as AgentManifestInfo);
    }
    return result;
  }

  async function putAgent(fullName: string, content: string, overwrite?: boolean) {
    const result = await withClient((c) => c.putAgent(fullName, content, overwrite));
    if (result?.success) {
      const entry = extractManifestEntry(result.data);
      if (entry) manifests.upsertAgent(entry as AgentManifestInfo);
    }
    return result;
  }

  async function deleteAgent(fullName: string) {
    const result = await withClient((c) => c.deleteAgent(fullName));
    if (result?.success) manifests.removeAgent(fullName);
    return result;
  }

  // ── Manifest: Utility ──

  async function resolveAgent(agentFullName: string, runtimeName?: string | null) {
    return withClient((c) => c.resolveAgent(agentFullName, runtimeName));
  }

  async function validateManifest(content: string, manifestType: string) {
    manifests.setValidating(true);
    manifests.setValidationResult(null);
    const result = await withClient((c) => c.validateManifest(content, manifestType));
    manifests.setValidating(false);
    if (result?.success) {
      const vr = extractValidationResult(result.data);
      if (vr) {
        manifests.setValidationResult(vr);
      }
    } else if (result && !result.success) {
      manifests.setValidationResult({
        is_valid: false,
        errors: [result.error ?? i18n.global.t("common.validationFailed")],
      });
    }
    return result;
  }

  return {
    client,
    connection,
    agents,
    actionChains,
    hitl,
    chat,
    changes,
    manifests,
    rooms,
    projects,
    error,
    connect,
    disconnect,
    autoConnect,
    sendMessage,
    sendAgentMessage,
    getAgentInfo,
    registerAbility,
    sendHitlResponse,
    spawnAgent,
    terminateAgent,
    agentCompactContext,
    agentReset,
    roomSend,
    listRooms,
    createRoom,
    updateRoom,
    archiveRoom,
    restoreRoom,
    deleteRoom,
    joinRoom,
    leaveRoom,
    getRoomLog,
    listProjects,
    createProject,
    updateProject,
    archiveProject,
    restoreProject,
    deleteProject,
    listAgents,
    listSessions,
    createSession,
    activateSession,
    archiveSession,
    deleteSession,
    listBranches,
    createBranch,
    activateBranch,
    archiveBranch,
    deleteBranch,
    switchRoom,
    switchProject,
    selectAgent,
    listManifestAbilities,
    getAbility,
    putAbility,
    deleteAbility,
    listManifestAgents,
    getAgent,
    putAgent,
    deleteAgent,
    resolveAgent,
    validateManifest,
  };
}
