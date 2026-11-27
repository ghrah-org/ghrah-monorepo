<!--
SPDX-FileCopyrightText: 2026 chenxya <chenxya@ghrah.org>

SPDX-License-Identifier: Apache-2.0
-->

<script setup lang="ts">
import {
  abilityControlClient,
  agentControlClient,
  type AgentDesiredState,
  type AgentTarget,
  HttpError,
  useAbilityAdminStore,
  useAbilityPackagesStore,
  useAgentsStore,
  useConnectionStore,
  useProjectsStore,
} from "@ghrah/observer-core";
import type { AbilityPackageGroupsView, AgentAdminView } from "@ghrah/protocol";
import { computed, onActivated, onDeactivated, onMounted, onUnmounted, ref, watch } from "vue";
import { useI18n } from "vue-i18n";
import { useObserver } from "@/composables/useObserver";
import SchemeAbilitiesPanel from "@/components/scheme-abilities-panel.vue";
import AbilityAdminView from "./ability-admin-view.vue";
import AbilityChangesView from "./ability-changes-view.vue";
import AbilityTreeView from "./ability-tree-view.vue";
import { availabilityKey, desiredStateKey, isAgentOnline, runtimeStatusKey } from "./agent-status";
import GrantEditor from "./grant-editor.vue";
import RespawnBanner from "./respawn-banner.vue";
import { type SurfaceStatus, surfaceStatus } from "./surface-status";
import ToolSelector from "./tool-selector.vue";
import ConfirmDialog from "@/components/ui/confirm-dialog.vue";
import UiButton from "@/components/ui/button.vue";

const props = defineProps<{ projectId: string; agentId: string }>();
const emit = defineEmits<{ deleted: [] }>();
const admin = useAbilityAdminStore();
const packages = useAbilityPackagesStore();
const agents = useAgentsStore();
const projects = useProjectsStore();
const connection = useConnectionStore();
const { turnAbort, error: observerError } = useObserver();
const { t } = useI18n();
const tabs = ["overview", "grants", "tools", "versions", "changes"] as const;
const tab = ref<(typeof tabs)[number]>("overview");
const agentGroups = ref<AbilityPackageGroupsView | null>(null);
const groupsLoading = ref(false);
const groupsFailed = ref(false);
const control = ref<AgentAdminView | null>(null);
const controlError = ref("");
const pendingAction = ref<"abort" | "start" | "stop" | "delete" | null>(null);
const pendingConfirm = ref<"stop" | "delete" | null>(null);
const notice = ref<{ kind: "ok" | "error"; text: string } | null>(null);
let shown = false;
let groupRequest = 0;
let controlRequest = 0;

const record = computed(() => {
  const current = admin.record(props.projectId, props.agentId);
  if (!current) return null;
  return {
    loading: current.loading,
    failed: current.failed,
    errorMessage: current.errorMessage,
    conflict: current.conflict,
    revision: current.revision,
    admin: current.admin,
    tree: current.tree,
    changes: current.changes.slice(),
    hasMore: current.hasMore,
  };
});
const treeEnabled = computed(() => packages.status?.features.tree === true);
const view = computed(() => record.value?.admin ?? null);
const surface = computed<SurfaceStatus | null>(() =>
  view.value ? surfaceStatus(view.value) : null,
);
const packagesReadable = computed(() => packages.status?.features.packages_read === true);

function originText(origin: "builtin" | "installed" | null): string {
  if (origin === "builtin") return t("assembly.catalog.origin.builtin");
  if (origin === "installed") return t("assembly.catalog.origin.installed");
  return t("assembly.catalog.origin.missing");
}

function problemText(problem: string): string {
  if (problem === "definition_missing") return t("assembly.versions.missingDefinition");
  if (problem.startsWith("definition_unreadable")) {
    return t("assembly.versions.unreadableDefinition");
  }
  return problem;
}

function groupKey(group: AbilityPackageGroupsView["groups"][number]): string {
  if (group.full_name) return group.full_name;
  return group.versions
    .map((version) => version.project?.digest ?? `${version.origin ?? ""}:${version.version ?? ""}`)
    .join("|");
}

async function loadGroups(): Promise<void> {
  const current = ++groupRequest;
  if (!packagesReadable.value) {
    agentGroups.value = null;
    groupsFailed.value = false;
    groupsLoading.value = false;
    return;
  }
  groupsLoading.value = true;
  groupsFailed.value = false;
  try {
    const next = await abilityControlClient(props.projectId).agentPackageGroups(props.agentId);
    if (current !== groupRequest) return;
    agentGroups.value = next;
  } catch {
    if (current !== groupRequest) return;
    agentGroups.value = null;
    groupsFailed.value = true;
  } finally {
    if (current === groupRequest) groupsLoading.value = false;
  }
}

const canWrite = computed(() => {
  const status = packages.status;
  if (!status || status.ability_source === "legacy" || status.legacy_deprecated) return false;
  return status.features.grants_write === true;
});

// ── Agent 生命周期操作条 ──

const project = computed(() => projects.projects.get(props.projectId) ?? null);
const recordRow = computed(
  () => project.value?.agents?.find((item) => item.agent_id === props.agentId) ?? null,
);
const live = computed(() =>
  agents.getAgent({ projectId: props.projectId, agentId: props.agentId }),
);
const agentName = computed(
  () =>
    live.value?.agentName ||
    recordRow.value?.name ||
    control.value?.specification.name ||
    props.agentId,
);
const agentTarget = computed<AgentTarget>(() => ({
  projectId: props.projectId,
  agentId: props.agentId,
  agentName: agentName.value,
}));
const desired = computed(
  () => control.value?.specification.desired_state ?? recordRow.value?.desired_state ?? "",
);
const availability = computed(() => control.value?.runtime.availability ?? "unknown");
const liveRuntime = computed(() => live.value?.runtimeStatus ?? "");
const busy = computed(() => control.value?.runtime.busy === true);
const online = computed(() => isAgentOnline(availability.value, liveRuntime.value));
const connected = computed(() => connection.state === "connected");
const projectVersion = computed(
  () => control.value?.specification.project_version ?? project.value?.version ?? 0,
);
const canStop = computed(() => online.value && pendingAction.value === null);
const canStart = computed(
  () => !online.value && availability.value !== "unknown" && pendingAction.value === null,
);
const canAbort = computed(() => online.value && connected.value && pendingAction.value === null);
const canDelete = computed(() => !busy.value && pendingAction.value === null);
const desiredText = computed(() => t(`assembly.agent.desired.${desiredStateKey(desired.value)}`));
const runtimeText = computed(() =>
  t(`assembly.agent.availability.${availabilityKey(availability.value)}`),
);
const runtimeStatus = computed(() =>
  runtimeStatusKey(recordRow.value?.runtime_status ?? liveRuntime.value),
);
const showRuntimeStatus = computed(
  () =>
    runtimeStatus.value === "pending" ||
    runtimeStatus.value === "failed" ||
    runtimeStatus.value === "lost",
);
const runtimeStatusText = computed(() => t(`assembly.agent.status.${runtimeStatus.value}`));

/** HTTP 失败 → 可读文案；命令层稳定码优先于通用 message。 */
function describeError(error: unknown, action: "start" | "stop" | "delete" | null = null): string {
  if (error instanceof HttpError) {
    if (error.status === 0) return t("assembly.agent.notConnected");
    if (error.code === "capability_unavailable") return t("assembly.agent.disabled");
    if (error.code === "conflict") return t("assembly.agent.conflict");
    if (error.code === "agent_busy") return t("assembly.agent.busyRejected");
    if (error.code === "not_found") return t("assembly.agent.notFound");
    if (action === "start") return t("assembly.agent.startFailed", { reason: error.message });
    if (action === "stop") return t("assembly.agent.stopFailed", { reason: error.message });
    if (action === null) return t("assembly.agent.controlFailed", { reason: error.message });
    return error.message;
  }
  const reason = error instanceof Error ? error.message : String(error);
  if (action === "start") return t("assembly.agent.startFailed", { reason });
  if (action === "stop") return t("assembly.agent.stopFailed", { reason });
  if (action === null) return t("assembly.agent.controlFailed", { reason });
  return reason;
}

async function refreshControl(): Promise<void> {
  const current = ++controlRequest;
  try {
    const next = await agentControlClient(props.projectId).get(props.agentId);
    if (current !== controlRequest) return;
    control.value = next;
    controlError.value = "";
  } catch (error) {
    if (current !== controlRequest) return;
    control.value = null;
    controlError.value = describeError(error);
  }
}

async function applyDesiredState(state: AgentDesiredState): Promise<void> {
  if (pendingAction.value !== null) return;
  const action = state === "running" ? "start" : "stop";
  pendingAction.value = action;
  notice.value = null;
  try {
    const view = await agentControlClient(props.projectId).setDesiredState(
      props.agentId,
      state,
      projectVersion.value,
    );
    control.value = view;
    controlError.value = "";
    notice.value = {
      kind: "ok",
      text: action === "start" ? t("assembly.agent.startDone") : t("assembly.agent.stopDone"),
    };
    void admin.markStale(props.projectId, props.agentId);
  } catch (error) {
    notice.value = { kind: "error", text: describeError(error, action) };
    void refreshControl();
  } finally {
    pendingAction.value = null;
    // 启动要等装配完成、停止要等运行态回收，回执里的运行态还不稳，补读收敛。
    nudgeControl();
  }
}

function start(): void {
  void applyDesiredState("running");
}

async function stop(): Promise<void> {
  pendingConfirm.value = null;
  await applyDesiredState("stopped");
}

async function abort(): Promise<void> {
  if (!canAbort.value) return;
  pendingAction.value = "abort";
  notice.value = null;
  try {
    const result = await turnAbort(agentTarget.value);
    if (result === null) {
      notice.value = {
        kind: "error",
        text: observerError.value ?? t("assembly.agent.notConnected"),
      };
      return;
    }
    if (!result.success) {
      const code = result.error ?? "";
      const text =
        code === "turn_not_found"
          ? t("assembly.agent.abortNoTurn")
          : code === "agent_offline"
            ? t("assembly.agent.offline")
            : code || t("assembly.agent.abortFailed");
      notice.value = { kind: "error", text };
      return;
    }
    const data = (result.data ?? {}) as { aborted?: boolean; already_terminal?: boolean };
    notice.value = {
      kind: "ok",
      text:
        data.aborted === true
          ? t("assembly.agent.abortDone")
          : data.already_terminal === true
            ? t("assembly.agent.abortAlreadyDone")
            : t("assembly.agent.abortDone"),
    };
  } finally {
    pendingAction.value = null;
    // 中断只改轮次与运行态、不改项目版本；且 Core 侧 busy 释放略晚于命令回执，
    // 单次补读常常仍读到 true，因此用一串短延迟补读收敛。
    nudgeControl();
  }
}

function finishDeleted(message: string): void {
  admin.clearAgent(props.projectId, props.agentId);
  notice.value = { kind: "ok", text: message };
  emit("deleted");
}

async function remove(): Promise<void> {
  pendingConfirm.value = null;
  if (pendingAction.value !== null) return;
  pendingAction.value = "delete";
  notice.value = null;
  try {
    await agentControlClient(props.projectId).delete(props.agentId, projectVersion.value);
    finishDeleted(t("assembly.agent.deleteDone"));
  } catch (error) {
    if (error instanceof HttpError && error.code === "not_found") {
      finishDeleted(t("assembly.agent.notFound"));
      return;
    }
    notice.value = { kind: "error", text: describeError(error, "delete") };
    void refreshControl();
  } finally {
    pendingAction.value = null;
  }
}

function refresh(): void {
  if (pendingAction.value !== null) return;
  void admin.load(props.projectId, props.agentId);
  void refreshControl();
}

/**
 * 运行态短轮询。
 *
 * ``busy`` 只在 GET /agents/{id} 的运行态里，轮次开始/结束既不推进项目版本、
 * 也不产生 observer 侧已投影的 turn 事件，所以条上的「处理中」与删除可用性
 * 必须自己跟上。页面不可见或动作进行中时不发请求。
 */
const POLL_INTERVAL_MS = 2000;
let pollTimer: ReturnType<typeof setInterval> | null = null;
const settleTimers: ReturnType<typeof setTimeout>[] = [];

/** 动作结束后的短延迟补读：命令回执与 Core 侧释放/装配不同步时收敛条上状态。 */
function nudgeControl(delaysMs: readonly number[] = [0, 400, 1200]) {
  for (const delay of delaysMs) {
    settleTimers.push(
      setTimeout(() => {
        if (shown) void refreshControl();
      }, delay),
    );
  }
}

function clearSettleTimers() {
  while (settleTimers.length) {
    clearTimeout(settleTimers.pop() as ReturnType<typeof setTimeout>);
  }
}

function startPolling() {
  if (pollTimer !== null) return;
  pollTimer = setInterval(() => {
    if (pendingAction.value !== null) return;
    if (typeof document !== "undefined" && document.visibilityState === "hidden") return;
    void refreshControl();
  }, POLL_INTERVAL_MS);
}

function stopPolling() {
  clearSettleTimers();
  if (pollTimer === null) return;
  clearInterval(pollTimer);
  pollTimer = null;
}

function show() {
  if (shown) return;
  shown = true;
  startPolling();
  if (treeEnabled.value) void admin.setVisible(props.projectId, props.agentId, true);
}

function hide() {
  if (!shown) return;
  shown = false;
  stopPolling();
  void admin.setVisible(props.projectId, props.agentId, false);
}

watch(
  () => [props.projectId, props.agentId] as const,
  (next, previous) => {
    if (!shown || !previous) return;
    if (previous[0] !== next[0] || previous[1] !== next[1]) {
      void admin.setVisible(previous[0], previous[1], false);
      if (treeEnabled.value) void admin.setVisible(next[0], next[1], true);
    }
  },
);

watch(treeEnabled, (enabled) => {
  if (!shown) return;
  void admin.setVisible(props.projectId, props.agentId, enabled);
});

watch(
  () =>
    [tab.value, props.projectId, props.agentId, packages.status?.features.packages_read] as const,
  () => {
    if (tab.value === "versions") void loadGroups();
  },
);

watch(
  () => [props.projectId, props.agentId] as const,
  () => {
    pendingConfirm.value = null;
    notice.value = null;
    void refreshControl();
  },
  { immediate: true },
);

watch(
  () => live.value?.runtimeStatus,
  (next, previous) => {
    if (next !== previous) void refreshControl();
  },
);

watch(
  () => project.value?.version,
  (next, previous) => {
    if (next !== previous) void refreshControl();
  },
);

onMounted(show);
onActivated(show);
onDeactivated(hide);
onUnmounted(hide);
</script>

<template>
  <section class="assembly-detail">
    <header class="assembly-agent-bar">
      <div class="assembly-agent-identity">
        <strong>{{ agentName }}</strong>
        <code>{{ agentId }}</code>
        <span>{{ desiredText }}</span>
        <span>{{ runtimeText }}</span>
        <span v-if="showRuntimeStatus">{{ runtimeStatusText }}</span>
        <span v-if="busy" class="assembly-agent-busy">{{ t("assembly.agent.busy") }}</span>
      </div>
      <div class="assembly-agent-actions">
        <UiButton
          v-if="online"
          class="assembly-agent-stop"
          type="button"
          variant="primary"
          :disabled="!canStop"
          @click="pendingConfirm = 'stop'"
        >
          {{ t("assembly.agent.stop") }}
        </UiButton>
        <UiButton
          v-else
          class="assembly-agent-start"
          type="button"
          variant="primary"
          :disabled="!canStart"
          @click="start"
        >
          {{ t("assembly.agent.start") }}
        </UiButton>
        <UiButton
          class="assembly-agent-abort"
          type="button"
          variant="secondary"
          :disabled="!canAbort"
          @click="abort"
        >
          {{ t("assembly.agent.abort") }}
        </UiButton>
        <UiButton
          class="assembly-agent-delete"
          type="button"
          variant="danger"
          :disabled="!canDelete"
          @click="pendingConfirm = 'delete'"
        >
          {{ t("assembly.agent.delete") }}
        </UiButton>
        <UiButton
          class="assembly-agent-refresh"
          type="button"
          variant="secondary"
          :disabled="pendingAction !== null"
          @click="refresh"
        >
          {{ t("assembly.agent.refresh") }}
        </UiButton>
      </div>
      <p v-if="controlError" class="assembly-note assembly-agent-note">{{ controlError }}</p>
      <p
        v-if="notice"
        :class="['assembly-agent-notice', `assembly-agent-notice-${notice.kind}`]"
        role="status"
      >
        {{ notice.text }}
      </p>
    </header>
    <ConfirmDialog
      v-if="pendingConfirm === 'stop'"
      :title="t('assembly.agent.stopTitle', { name: agentName })"
      :message="t('assembly.agent.stopMessage')"
      :confirm-label="t('assembly.agent.stop')"
      danger
      @confirm="stop"
      @cancel="pendingConfirm = null"
    />
    <ConfirmDialog
      v-if="pendingConfirm === 'delete'"
      :title="t('assembly.agent.deleteTitle', { name: agentName })"
      :message="t('assembly.agent.deleteMessage')"
      :confirm-label="t('assembly.agent.delete')"
      danger
      @confirm="remove"
      @cancel="pendingConfirm = null"
    />
    <p v-if="!treeEnabled" class="assembly-state">
      {{
        packages.status?.legacy_deprecated || packages.status?.ability_source === "legacy"
          ? t("assembly.statusLegacy")
          : t("assembly.statusUnavailable")
      }}
    </p>
    <template v-else>
      <p v-if="record?.loading" class="assembly-state">{{ t("assembly.loading") }}</p>
      <p v-if="record?.failed" class="assembly-state assembly-state-alert" role="alert">
        {{ t("assembly.loadFailed") }}
      </p>
      <p v-if="record?.failed && record.errorMessage" class="assembly-note">
        {{ record.errorMessage }}
      </p>
      <UiButton
        v-if="record?.failed"
        type="button"
        variant="secondary"
        @click="admin.load(projectId, agentId)"
      >
        {{ t("assembly.retry") }}
      </UiButton>
      <RespawnBanner
        :status="surface"
        :pending-added="view?.surface.pending_respawn.added ?? []"
        :pending-removed="view?.surface.pending_respawn.removed ?? []"
        :conflict="record?.conflict === true"
        :revision="record?.revision ?? view?.revision ?? null"
      />
      <div class="assembly-tabs" role="tablist">
        <button
          v-for="name in tabs"
          :key="name"
          type="button"
          class="assembly-tab"
          :class="{ 'assembly-tab-selected': tab === name }"
          role="tab"
          :aria-selected="tab === name"
          @click="tab = name"
        >
          {{ t(`assembly.tabs.${name}`) }}
        </button>
      </div>
      <div v-if="tab === 'overview' && view" class="assembly-detail-body assembly-facts">
        <p>{{ t("assembly.overview.lifecycle", { state: view.lifecycle.state }) }}</p>
        <p>{{ t("assembly.overview.revision", { revision: view.revision }) }}</p>
        <p v-if="view.run_id">{{ t("assembly.overview.run", { run: view.run_id }) }}</p>
        <p v-else>{{ t("assembly.overview.noRun") }}</p>
        <p v-if="view.lifecycle.pending_reasons.length">
          {{
            t("assembly.overview.pending", { reasons: view.lifecycle.pending_reasons.join(", ") })
          }}
        </p>
        <p v-if="view.grant_policy">
          {{
            t("assembly.overview.policy", {
              mode: view.grant_policy.mode,
              rules: view.grant_policy.rules,
            })
          }}
        </p>
        <p v-else>{{ t("assembly.overview.noPolicy") }}</p>
        <p>{{ t("assembly.overview.effectiveOnly") }}</p>
      </div>
      <div v-else-if="tab === 'grants' && view" class="assembly-detail-body">
        <template v-if="canWrite">
          <GrantEditor
            v-for="grant in view.grants"
            :key="grant.alias"
            :grant="grant"
            :expected-revision="record?.revision ?? view.revision"
            :busy="record?.loading === true"
            @save="admin.replaceGrant(projectId, agentId, $event)"
            @revoke="admin.revokeGrant(projectId, agentId, grant.alias)"
          />
          <p v-if="view.grants.length === 0" class="assembly-state assembly-state-empty">
            {{ t("assembly.grants.empty") }}
          </p>
        </template>
        <AbilityAdminView v-else :admin="view" />
      </div>
      <div v-else-if="tab === 'tools'" class="assembly-detail-body">
        <template v-if="view">
          <p v-if="canWrite" class="assembly-note">{{ t("assembly.surface.selection") }}</p>
          <p v-else class="assembly-note">{{ t("assembly.surface.readonly") }}</p>
          <ToolSelector
            v-if="canWrite"
            :grants="view.grants"
            :bound-tools="view.bound_tools"
            :expected-revision="record?.revision ?? view.revision"
            :busy="record?.loading === true"
            @bind="admin.bindTool(projectId, agentId, $event)"
            @unbind="admin.unbindTool(projectId, agentId, $event)"
          />
          <div class="assembly-facts">
            <p>{{ t("assembly.surface.mode", { mode: view.surface.mode }) }}</p>
            <p>
              {{ t("assembly.surface.effective") }}
              {{ view.surface.effective.map((entry) => entry.tool_name).join(", ") }}
            </p>
            <p v-if="surface === 'unmaterialized'">{{ t("assembly.state.runtimeUnobserved") }}</p>
            <p v-else-if="surface === 'unobserved'">
              {{ t("assembly.surface.runtimeMissing") }}
            </p>
            <p v-else>
              {{ t("assembly.surface.runtime") }}
              {{ (view.surface.current_runtime ?? []).join(", ") }}
            </p>
            <p v-if="view.surface.effective.some((entry) => !entry.available)">
              {{ t("assembly.surface.visibleNotExecutable") }}
            </p>
            <p>
              {{
                t("assembly.surface.pendingAdded", {
                  tools: view.surface.pending_respawn.added.join(", "),
                })
              }}
            </p>
            <p>
              {{
                t("assembly.surface.pendingRemoved", {
                  tools: view.surface.pending_respawn.removed.join(", "),
                })
              }}
            </p>
          </div>
          <AbilityTreeView :tree="record?.tree ?? null" />
        </template>
        <SchemeAbilitiesPanel readonly :project-id="projectId" :agent-id="agentId" />
      </div>
      <div v-else-if="tab === 'versions'" class="assembly-detail-body">
        <p v-if="groupsLoading || (packages.loading && !packages.status)" class="assembly-state">
          {{ t("assembly.loading") }}
        </p>
        <p v-else-if="!packagesReadable" class="assembly-state">
          {{ t("assembly.catalog.packagesHidden") }}
        </p>
        <p v-else-if="groupsFailed" class="assembly-state assembly-state-alert" role="alert">
          {{ t("assembly.versions.groupsFailed") }}
        </p>
        <p v-else-if="!agentGroups?.groups.length" class="assembly-state assembly-state-empty">
          {{ t("assembly.catalog.empty") }}
        </p>
        <ul v-else class="assembly-groups">
          <li v-for="group in agentGroups.groups" :key="groupKey(group)" class="assembly-group">
            <h3>{{ group.title || group.full_name || t("assembly.versions.unreadable") }}</h3>
            <p v-if="group.full_name">
              <code>{{ group.full_name }}</code>
            </p>
            <ul>
              <li
                v-for="(version, index) in group.versions"
                :key="`${version.version ?? ''}:${version.project?.digest ?? index}`"
              >
                <span>{{ version.version }}</span>
                <span>{{ originText(version.origin) }}</span>
                <span>
                  {{
                    version.project?.committed
                      ? t("assembly.versions.committed")
                      : t("assembly.versions.notCommitted")
                  }}
                </span>
                <span v-if="version.project?.selected">{{ t("assembly.versions.selected") }}</span>
                <span v-if="version.agent?.granted">
                  {{ t("assembly.versions.granted", { alias: version.agent.alias }) }}
                </span>
                <span v-else>{{ t("assembly.versions.notGranted") }}</span>
                <span v-if="version.problem">{{ problemText(version.problem) }}</span>
              </li>
            </ul>
          </li>
        </ul>
      </div>
      <AbilityChangesView
        v-else-if="tab === 'changes'"
        :changes="record?.changes ?? []"
        :has-more="record?.hasMore ?? false"
        :loading="record?.loading ?? false"
        @more="admin.loadMore(projectId, agentId)"
      />
    </template>
  </section>
</template>

<style scoped>
.assembly-detail,
.assembly-detail-body {
  display: flex;
  min-width: 0;
  flex-direction: column;
  gap: 0.75rem;
  overflow-wrap: anywhere;
}

.assembly-detail {
  color: var(--color-text);
  font-size: var(--font-ui-body);
}

.assembly-tabs {
  display: flex;
  flex-wrap: wrap;
  gap: 0.5rem;
}

.assembly-tab {
  padding: 0.25rem 0.75rem;
  border: 1px solid var(--color-border);
  border-radius: var(--radius-md);
  background: var(--color-surface);
  color: var(--color-text-muted);
  font-size: var(--font-ui-note);
  cursor: pointer;
}

.assembly-tab-selected {
  border-color: var(--color-selected-line);
  background: var(--color-selected);
  color: var(--color-text);
}

.assembly-facts {
  display: flex;
  flex-direction: column;
  gap: 0.25rem;
  padding: 0.75rem 1rem;
  border: 1px solid var(--color-border);
  border-radius: var(--radius-md);
  background: var(--color-surface);
}

.assembly-facts p,
.assembly-note,
.assembly-state {
  margin: 0;
}

.assembly-note {
  color: var(--color-text-muted);
  font-size: var(--font-ui-note);
  line-height: 1.5;
}

.assembly-facts p {
  color: var(--color-text-muted);
  font-size: var(--font-ui-note);
  line-height: 1.5;
}

.assembly-state {
  padding: 0.75rem 1rem;
  border: 1px solid var(--color-border);
  border-radius: var(--radius-md);
  background: var(--color-surface-2);
  color: var(--color-text-muted);
  font-size: var(--font-ui-note);
  line-height: 1.5;
}

.assembly-state-alert {
  border-color: var(--color-danger);
  background: color-mix(in srgb, var(--color-danger) 12%, var(--color-surface));
  color: var(--color-danger);
}

.assembly-state-empty {
  background: var(--color-surface);
  text-align: center;
}

.assembly-groups,
.assembly-groups ul {
  display: flex;
  min-width: 0;
  flex-direction: column;
  gap: 0.75rem;
  margin: 0;
  padding: 0;
  list-style: none;
}

.assembly-group {
  min-width: 0;
  padding: 0.75rem 1rem;
  border: 1px solid var(--color-border);
  border-radius: var(--radius-md);
  background: var(--color-surface);
}

.assembly-group h3 {
  margin: 0;
  color: var(--color-text);
  font-size: var(--font-ui-body);
  font-weight: 600;
}

.assembly-group li {
  display: flex;
  min-width: 0;
  flex-wrap: wrap;
  gap: 0.25rem 0.75rem;
  color: var(--color-text-muted);
  font-size: var(--font-ui-note);
}

.assembly-group code {
  font-size: var(--font-ui-code);
}

.assembly-agent-bar {
  display: flex;
  flex-direction: column;
  gap: 0.5rem;
  padding: 0.75rem 1rem;
  border: 1px solid var(--color-border);
  border-radius: var(--radius-md);
  background: var(--color-surface-2);
}

.assembly-agent-identity,
.assembly-agent-actions {
  display: flex;
  flex-wrap: wrap;
  align-items: center;
  gap: 0.5rem 0.75rem;
}

.assembly-agent-identity strong {
  color: var(--color-text);
  font-size: var(--font-ui-body);
}

.assembly-agent-identity code {
  color: var(--color-text-muted);
  font-size: var(--font-ui-code);
}

.assembly-agent-identity span {
  color: var(--color-text-muted);
  font-size: var(--font-ui-note);
}

.assembly-agent-busy {
  padding: 0 0.5rem;
  border: 1px solid var(--color-border);
  border-radius: var(--radius-md);
  background: var(--color-surface);
}

.assembly-agent-note {
  margin: 0;
}

.assembly-agent-notice {
  margin: 0;
  font-size: var(--font-ui-note);
  line-height: 1.5;
}

.assembly-agent-notice-ok {
  color: var(--color-text-muted);
}

.assembly-agent-notice-error {
  color: var(--color-danger);
}
</style>
