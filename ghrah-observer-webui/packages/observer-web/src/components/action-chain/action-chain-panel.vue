<!--
SPDX-FileCopyrightText: 2026 chenxya <chenxya@ghrah.org>

SPDX-License-Identifier: Apache-2.0
-->

<script setup lang="ts">
import {
  type AgentTarget,
  type ChainTarget,
  getVisibleNodes,
  useActionChainsStore,
  useAgentsStore,
  useBranchesStore,
  useSessionsStore,
} from "@ghrah/observer-core";
import { computed, nextTick, ref, watch } from "vue";
import { useI18n } from "vue-i18n";
import { useObserver } from "@/composables/useObserver";
import { useTabScrollRestore } from "@/composables/useTabScrollRestore";
import ActionChainBranchBar from "./action-chain-branch-bar.vue";
import ActionChainCanvas from "./action-chain-canvas.vue";
import ActionChainDetail from "./action-chain-detail.vue";
import ActionChainSessionPicker from "./action-chain-session-picker.vue";
import { createLayoutCache } from "./layout-cache.js";
import { layoutGraph } from "./layout-graph.js";

const { t } = useI18n();

const props = defineProps<{
  agent: AgentTarget;
}>();

const chains = useActionChainsStore();
const agents = useAgentsStore();
const sessions = useSessionsStore();
const branches = useBranchesStore();
const {
  activateSession,
  activateBranch,
  createSession,
  createBranch,
  archiveSession,
  deleteSession,
  archiveBranch,
  deleteBranch,
  agentReset,
} = useObserver();

// store 中的 AgentInfo 含 clusterId/config 等额外字段，对外（RPC/store 键）只投影纯 AgentTarget
const agentTarget = computed<AgentTarget>(() => {
  const resolved = agents.getAgent(props.agent);
  return resolved
    ? { projectId: resolved.projectId, agentId: resolved.agentId, agentName: resolved.agentName }
    : props.agent;
});
const selectedAgentName = computed(() => agentTarget.value.agentName);

type SwitchKind =
  | "session"
  | "branch"
  | "new-session"
  | "new-branch"
  | "archive-session"
  | "delete-session"
  | "archive-branch"
  | "delete-branch"
  | "retry"
  | "reset-agent";
const switching = ref<SwitchKind | null>(null);
const actionError = ref<string | null>(null);
const actionNotice = ref<string | null>(null);

// ── 运行态（runtimeActive*）：只由后端事件/同步结果推进 ──
const runtimeSessionId = computed(() =>
  agentTarget.value ? sessions.activeSessionId(agentTarget.value) : null,
);
const runtimeSessionTarget = computed(() => {
  const agent = agentTarget.value;
  const sessionId = agent ? sessions.activeSessionId(agent) : null;
  return agent && sessionId ? { ...agent, sessionId } : null;
});
const runtimeBranchId = computed(() =>
  runtimeSessionTarget.value ? branches.activeBranchId(runtimeSessionTarget.value) : null,
);

// ── 展示态（viewed*）：面板本地选择，不发运行时命令 ──
const viewedSessionId = computed(() =>
  agentTarget.value ? sessions.viewedSessionId(agentTarget.value) : null,
);
const viewedSessionTarget = computed(() => {
  const agent = agentTarget.value;
  const sessionId = agent ? sessions.viewedSessionId(agent) : null;
  return agent && sessionId ? { ...agent, sessionId } : null;
});
/** undefined = 尚未选择（回退运行态 Branch）；null = 查看全部 Branch。 */
const viewedBranchSelection = computed(() =>
  viewedSessionTarget.value ? branches.viewedBranchId(viewedSessionTarget.value) : undefined,
);

const sessionOptions = computed(() =>
  agentTarget.value ? sessions.sessionsForAgent(agentTarget.value) : [],
);
const branchOptions = computed(() =>
  viewedSessionTarget.value ? branches.branchesForSession(viewedSessionTarget.value) : [],
);

function failureOf(
  result:
    { error?: string | null; error_detail?: string | null; success: boolean } | null | undefined,
): string {
  return result?.error_detail ?? result?.error ?? t("actionChain.switchFailed");
}

// ── 查看动作（普通切换，无运行时副作用） ──
function viewSession(sessionId: string) {
  const agent = agentTarget.value;
  if (!agent || !sessionId) return;
  sessions.viewSession(agent, sessionId);
  const target = { ...agent, sessionId };
  // 切换 Session 后展示态 Branch 回到"未选择"（回退该 Session 运行 Branch）
  branches.viewBranch(target, undefined);
}

function viewBranch(branchValue: string) {
  const session = viewedSessionTarget.value;
  if (!session) return;
  if (branchValue === "__all__") branches.viewBranch(session, null);
  else branches.viewBranch(session, branchValue);
}

// ── 显式激活动作（菜单按钮 + 确认，产生运行时副作用） ──
async function confirmActivateSession() {
  const agent = agentTarget.value;
  const sessionId = viewedSessionId.value;
  if (!agent || !sessionId || sessionId === runtimeSessionId.value) return;
  if (!window.confirm(t("actionChain.activateSessionConfirm"))) return;
  switching.value = "session";
  actionError.value = null;
  try {
    const result = await activateSession({ ...agent, sessionId });
    if (!result?.success) actionError.value = failureOf(result);
  } finally {
    switching.value = null;
  }
}

async function confirmActivateBranch() {
  const session = viewedSessionTarget.value;
  const branchId = viewedBranchSelection.value;
  if (!session || !branchId) return;
  if (branchId === runtimeBranchId.value) return;
  if (!window.confirm(t("actionChain.activateBranchConfirm"))) return;
  switching.value = "branch";
  actionError.value = null;
  try {
    const target: ChainTarget = { ...session, branchId };
    const result = await activateBranch(target);
    if (!result?.success) actionError.value = failureOf(result);
  } finally {
    switching.value = null;
  }
}

async function startSession() {
  const agent = agentTarget.value;
  if (!agent) return;
  switching.value = "new-session";
  actionError.value = null;
  try {
    const result = await createSession(agent);
    if (!result?.success) {
      actionError.value = failureOf(result);
      return;
    }
    const data = result.data as { session_id?: string } | null;
    // 新建 Session 只把展示切过去，不隐式激活运行态（D6 裁决）。
    if (data?.session_id) sessions.viewSession(agent, data.session_id);
  } finally {
    switching.value = null;
  }
}

async function startBranch() {
  const session = viewedSessionTarget.value;
  if (!session) return;
  switching.value = "new-branch";
  actionError.value = null;
  try {
    const name = t("actionChain.newBranchName", {
      index: branches.branchesForSession(session).length + 1,
    });
    const result = await createBranch(session, name);
    if (!result?.success) {
      actionError.value = failureOf(result);
      return;
    }
    const data = result.data as { branch_id?: string } | null;
    // 新建 Branch 只把展示切过去，不隐式激活运行态（D6 裁决）。
    if (data?.branch_id) branches.viewBranch(session, data.branch_id);
  } finally {
    switching.value = null;
  }
}

// ── 显式管理动作（confirm + 命令；成功后 store 由事件面推进，此处不写投影） ──

async function confirmArchiveSession() {
  const session = viewedSessionTarget.value;
  if (!session) return;
  if (!window.confirm(t("actionChain.archiveSessionConfirm"))) return;
  switching.value = "archive-session";
  actionError.value = null;
  try {
    const result = await archiveSession(session);
    if (!result?.success) actionError.value = failureOf(result);
  } finally {
    switching.value = null;
  }
}

async function confirmDeleteSession() {
  const session = viewedSessionTarget.value;
  if (!session || session.sessionId === runtimeSessionId.value) return;
  if (!window.confirm(t("actionChain.deleteSessionConfirm"))) return;
  switching.value = "delete-session";
  actionError.value = null;
  try {
    const result = await deleteSession(session);
    if (!result?.success) actionError.value = failureOf(result);
  } finally {
    switching.value = null;
  }
}

async function confirmArchiveBranch() {
  const session = viewedSessionTarget.value;
  const branchId = viewedBranchSelection.value;
  if (!session || !branchId) return;
  if (!window.confirm(t("actionChain.archiveBranchConfirm"))) return;
  switching.value = "archive-branch";
  actionError.value = null;
  try {
    const result = await archiveBranch({ ...session, branchId });
    if (!result?.success) actionError.value = failureOf(result);
  } finally {
    switching.value = null;
  }
}

async function confirmDeleteBranch() {
  const session = viewedSessionTarget.value;
  const branchId = viewedBranchSelection.value;
  if (!session || !branchId || branchId === runtimeBranchId.value) return;
  if (!window.confirm(t("actionChain.deleteBranchConfirm"))) return;
  switching.value = "delete-branch";
  actionError.value = null;
  try {
    const result = await deleteBranch({ ...session, branchId });
    if (!result?.success) actionError.value = failureOf(result);
  } finally {
    switching.value = null;
  }
}

/** 从选中节点重试：confirm → createBranch(fromNodeId)；成功后仅切查看（D6），绝不隐式激活。 */
async function retryFromSelectedNode() {
  const session = viewedSessionTarget.value;
  const node = selectedNode.value;
  if (!session || !node?.id) return;
  if (!window.confirm(t("actionChain.retryFromNodeConfirm"))) return;
  switching.value = "retry";
  actionError.value = null;
  try {
    // n = 当前 Session 非 deleted Branch 数 + 1
    const count = branches
      .branchesForSession(session)
      .filter((item) => item.info.lifecycle !== "deleted").length;
    const name = t("actionChain.retryBranchName", { index: count + 1 });
    const result = await createBranch(session, name, { fromNodeId: node.id });
    if (!result?.success) {
      actionError.value = failureOf(result);
      return;
    }
    const data = result.data as { branch_id?: string } | null;
    if (data?.branch_id) branches.viewBranch(session, data.branch_id);
  } finally {
    switching.value = null;
  }
}

/** 重置 Agent：confirm → agentReset；新 Session 实体与运行标记由事件面推进（回执仅提示）。 */
async function confirmResetAgent() {
  const agent = agentTarget.value;
  if (!agent) return;
  if (!window.confirm(t("actionChain.resetAgentConfirm"))) return;
  switching.value = "reset-agent";
  actionError.value = null;
  actionNotice.value = null;
  try {
    const result = await agentReset(agent);
    if (!result?.success) {
      actionError.value = failureOf(result);
      return;
    }
    const data = result.data as { session_id?: string } | null;
    if (data?.session_id) {
      actionNotice.value = t("actionChain.resetAgentDone", { session: data.session_id });
    }
  } finally {
    switching.value = null;
  }
}

// ── 可视节点（getVisibleNodes：viewed Session + viewed/runtime Branch 回溯） ──
const visibleNodes = computed(() => {
  const agent = agentTarget.value;
  if (!agent) return [];
  return getVisibleNodes(sessions, branches, chains, agent);
});

/** 布局输入 branch：viewed Session 的非 deleted Branch（与 getVisibleNodes 选择口径一致）。 */
const visibleBranches = computed(() => {
  const session = viewedSessionTarget.value;
  if (!session) return [];
  if (viewedBranchSelection.value === null) {
    return branches
      .branchesForSession(session)
      .filter((item) => item.info.lifecycle !== "deleted")
      .map((item) => item.info);
  }
  if (viewedBranchSelection.value === undefined) {
    const branchId = runtimeBranchId.value;
    if (!branchId) return [];
    return branches
      .branchesForSession(session)
      .filter((item) => item.target.branchId === branchId && item.info.lifecycle !== "deleted")
      .map((item) => item.info);
  }
  return branches
    .branchesForSession(session)
    .filter(
      (item) =>
        item.target.branchId === viewedBranchSelection.value && item.info.lifecycle !== "deleted",
    )
    .map((item) => item.info);
});

// 画布布局（性能红线 2：append-only 增量缓存；键 = agent + session + branch 选择，
// 失配自动回退全量 layoutGraph。模块级 LRU，切换 agent/session 不清缓存）
const layoutCache = createLayoutCache();
const layoutScopeKey = computed(() => {
  const session = viewedSessionTarget.value;
  if (!session) return null;
  const selection = viewedBranchSelection.value;
  const branchPart = selection === undefined ? "runtime" : selection === null ? "all" : selection;
  return `${session.projectId}/${session.agentId}/${session.sessionId}/${branchPart}`;
});
const graph = computed(() => {
  const key = layoutScopeKey.value;
  if (!key) return layoutGraph(visibleNodes.value, []);
  return layoutCache.compute(key, visibleNodes.value, visibleBranches.value);
});

// 画布选中节点（阶段 3 详情区输入；面板本地持有）
const selectedNodeId = ref<string | null>(null);

/** 选中节点对象：从可视节点集中取（选中的节点必然在画布上）。 */
const selectedNode = computed(
  () => visibleNodes.value.find((item) => item.id === selectedNodeId.value) ?? null,
);

// 滚动恢复挂接画布内部视口（canvas 组件经 defineExpose 暴露；面板不重复包滚动层）。
// 链方向为 root → Head 自左向右：默认贴右端（最新节点），用户在历史区间浏览时不被新节点拽回。
const canvasRef = ref<InstanceType<typeof ActionChainCanvas> | null>(null);
const chainViewport = computed<HTMLElement | null>(() => canvasRef.value?.viewport ?? null);
const STICK_RIGHT_PX = 8;
const stickyRight = ref(true);

function isNearRight(): boolean {
  const el = chainViewport.value;
  if (!el) return true;
  return el.scrollWidth - el.scrollLeft - el.clientWidth <= STICK_RIGHT_PX;
}

const { onScroll: onChainScroll, applyScroll: applyChainScroll } = useTabScrollRestore(
  chainViewport,
  {
    contentKey: computed(() => graph.value.nodes.length),
    horizontalTarget: () => (stickyRight.value ? "end" : null),
  },
);

function handleChainScroll(): void {
  stickyRight.value = isNearRight();
  onChainScroll();
}

// 切换 viewed Session/Branch 视为“打开另一条链”：回到右端看最新节点。
watch(layoutScopeKey, () => {
  stickyRight.value = true;
  void nextTick(applyChainScroll);
});

const selectBranchValue = computed(() => {
  const selection = viewedBranchSelection.value;
  if (selection === undefined) return runtimeBranchId.value ?? "";
  return selection ?? "__all__";
});
</script>

<template>
  <div class="p-3 h-full flex flex-col">
    <div class="flex items-center justify-between mb-2">
      <h3 class="text-sm font-semibold uppercase tracking-wider text-gray-500 dark:text-gray-400">
        {{ t("actionChain.title") }}
      </h3>
      <span v-if="selectedAgentName" class="text-xs text-gray-500 dark:text-gray-400"
        >@{{ selectedAgentName }}</span
      >
    </div>

    <div v-if="agentTarget" class="flex items-center gap-2 flex-wrap mb-2">
      <ActionChainSessionPicker
        :sessions="sessionOptions"
        :viewed-session-id="viewedSessionId"
        :runtime-session-id="runtimeSessionId"
        :disabled="switching !== null"
        @view="viewSession"
        @activate="confirmActivateSession"
        @archive="confirmArchiveSession"
        @remove="confirmDeleteSession"
        @create="startSession"
      />
      <ActionChainBranchBar
        v-if="viewedSessionTarget"
        :branches="branchOptions"
        :selected-value="selectBranchValue"
        :runtime-branch-id="runtimeBranchId"
        :disabled="switching !== null"
        @view="viewBranch"
        @activate="confirmActivateBranch"
        @archive="confirmArchiveBranch"
        @remove="confirmDeleteBranch"
        @create="startBranch"
      />
      <button
        type="button"
        class="chain-reset-agent text-xs underline text-red-600 dark:text-red-400 hover:text-red-700 dark:hover:text-red-300 disabled:opacity-50 disabled:cursor-not-allowed"
        :disabled="switching !== null"
        @click="confirmResetAgent"
      >
        {{ t("actionChain.resetAgent") }}
      </button>
    </div>

    <div v-if="actionError" class="mb-2 text-xs text-red-600 dark:text-red-400" role="alert">
      {{ actionError }}
    </div>
    <div v-else-if="actionNotice" class="mb-2 text-xs text-emerald-600 dark:text-emerald-400">
      {{ actionNotice }}
    </div>

    <div
      v-if="!selectedAgentName"
      class="flex-1 flex items-center justify-center text-gray-400 dark:text-gray-600 text-sm italic"
    >
      {{ t("actionChain.selectAgent") }}
    </div>

    <div
      v-else-if="graph.nodes.length === 0"
      class="flex-1 flex items-center justify-center text-gray-400 dark:text-gray-600 text-sm italic"
    >
      {{ t("actionChain.empty") }}
    </div>

    <div
      v-else
      class="flex-1 flex flex-col border border-gray-200 dark:border-gray-700 rounded min-h-0"
    >
      <div
        class="px-2 py-1 bg-gray-50 dark:bg-gray-800 text-xs font-semibold text-gray-600 dark:text-gray-300 border-b border-gray-200 dark:border-gray-700"
      >
        @{{ selectedAgentName }}
      </div>
      <ActionChainCanvas
        ref="canvasRef"
        :class="{ 'ac-canvas--detail-open': selectedNode }"
        :graph="graph"
        :selected-node-id="selectedNodeId"
        @select="selectedNodeId = $event"
        @scroll="handleChainScroll"
      />
      <!-- 详情区（D4 裁决 A：画布下方展开；selectedNodeId 无效时占位提示） -->
      <div class="ac-detail-wrap" :class="{ 'ac-detail-wrap--empty': !selectedNode }">
        <ActionChainDetail :node="selectedNode" />
        <div v-if="selectedNode" class="ac-detail-actions">
          <button
            type="button"
            class="chain-retry-from-node text-xs underline text-gray-500 dark:text-gray-400 hover:text-gray-700 dark:hover:text-gray-200"
            :disabled="switching !== null"
            @click="retryFromSelectedNode"
          >
            {{ t("actionChain.retryFromNode") }}
          </button>
        </div>
      </div>
    </div>
  </div>
</template>
