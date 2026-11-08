<!--
SPDX-FileCopyrightText: 2026 chenxya <chenxya@ghrah.org>

SPDX-License-Identifier: Apache-2.0
-->

<script setup lang="ts">
import { useAbilityAdminStore, useAbilityPackagesStore } from "@ghrah/observer-core";
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
let shown = false;

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

onMounted(show);
onActivated(show);
onDeactivated(hide);
onUnmounted(hide);
</script>

<template>
  <section class="assembly-detail">
    <p v-if="!treeEnabled">
      {{
        packages.status?.legacy_deprecated || packages.status?.ability_source === "legacy"
          ? t("assembly.statusLegacy")
          : t("assembly.statusUnavailable")
      }}
    </p>
    <template v-else>
      <p v-if="record?.loading">{{ t("assembly.loading") }}</p>
      <p v-if="record?.failed" role="alert">{{ t("assembly.loadFailed") }}</p>
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
          role="tab"
          :aria-selected="tab === name"
          @click="tab = name"
        >
          {{ t(`assembly.tabs.${name}`) }}
        </button>
      </div>
      <div v-if="tab === 'overview' && view" class="assembly-detail-body">
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
          <p v-if="view.grants.length === 0">{{ t("assembly.grants.empty") }}</p>
        </template>
        <AbilityAdminView v-else :admin="view" />
      </div>
      <div v-else-if="tab === 'tools'" class="assembly-detail-body">
        <template v-if="view">
          <p v-if="canWrite">{{ t("assembly.surface.selection") }}</p>
          <p v-else>{{ t("assembly.surface.readonly") }}</p>
          <ToolSelector
            v-if="canWrite"
            :grants="view.grants"
            :bound-tools="view.bound_tools"
            :expected-revision="record?.revision ?? view.revision"
            :busy="record?.loading === true"
            @bind="admin.bindTool(projectId, agentId, $event)"
            @unbind="admin.unbindTool(projectId, agentId, $event)"
          />
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
          <AbilityTreeView :tree="record?.tree ?? null" />
        </template>
        <SchemeAbilitiesPanel readonly :project-id="projectId" :agent-id="agentId" />
      </div>
      <div v-else-if="tab === 'versions'" class="assembly-detail-body">
        <p>{{ t("assembly.versions.unavailable") }}</p>
        <ul>
          <li v-for="grant in view?.grants ?? []" :key="grant.alias">
            <code>{{ grant.alias }}</code>
            <span>{{ t("assembly.versions.current", { digest: grant.version_digest }) }}</span>
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
  min-width: 0;
  overflow-wrap: anywhere;
}

.assembly-detail {
  font-size: var(--font-ui-body);
}

.assembly-tabs {
  display: flex;
  flex-wrap: wrap;
  gap: 0.5rem;
}
</style>
