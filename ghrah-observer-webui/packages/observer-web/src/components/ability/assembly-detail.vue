<!--
SPDX-FileCopyrightText: 2026 chenxya <chenxya@ghrah.org>

SPDX-License-Identifier: Apache-2.0
-->

<script setup lang="ts">
import {
  abilityControlClient,
  useAbilityAdminStore,
  useAbilityPackagesStore,
} from "@ghrah/observer-core";
import type { AbilityPackageGroupsView } from "@ghrah/protocol";
import { computed, onActivated, onDeactivated, onMounted, onUnmounted, ref, watch } from "vue";
import { useI18n } from "vue-i18n";
import SchemeAbilitiesPanel from "@/components/scheme-abilities-panel.vue";
import AbilityAdminView from "./ability-admin-view.vue";
import AbilityChangesView from "./ability-changes-view.vue";
import AbilityTreeView from "./ability-tree-view.vue";
import GrantEditor from "./grant-editor.vue";
import RespawnBanner from "./respawn-banner.vue";
import ToolSelector from "./tool-selector.vue";
import UiButton from "@/components/ui/button.vue";

const props = defineProps<{ projectId: string; agentId: string }>();
const admin = useAbilityAdminStore();
const packages = useAbilityPackagesStore();
const { t } = useI18n();
const tabs = ["overview", "grants", "tools", "versions", "changes"] as const;
const tab = ref<(typeof tabs)[number]>("overview");
const agentGroups = ref<AbilityPackageGroupsView | null>(null);
const groupsLoading = ref(false);
const groupsFailed = ref(false);
let shown = false;
let groupRequest = 0;

const record = computed(() => {
  const current = admin.record(props.projectId, props.agentId);
  if (!current) return null;
  return {
    loading: current.loading,
    failed: current.failed,
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

function show() {
  if (shown) return;
  shown = true;
  if (treeEnabled.value) void admin.setVisible(props.projectId, props.agentId, true);
}

function hide() {
  if (!shown) return;
  shown = false;
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

onMounted(show);
onActivated(show);
onDeactivated(hide);
onUnmounted(hide);
</script>

<template>
  <section class="assembly-detail">
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
      <UiButton
        v-if="record?.failed"
        type="button"
        variant="secondary"
        @click="admin.load(projectId, agentId)"
      >
        {{ t("assembly.retry") }}
      </UiButton>
      <RespawnBanner
        :requires-respawn="view?.lifecycle.requires_respawn === true"
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
            <p v-if="view.surface.current_runtime">
              {{ t("assembly.surface.runtime") }}
              {{ view.surface.current_runtime.join(", ") }}
            </p>
            <p v-else>{{ t("assembly.surface.runtimeMissing") }}</p>
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
</style>
