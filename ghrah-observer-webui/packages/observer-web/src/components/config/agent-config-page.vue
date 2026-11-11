<!--
SPDX-FileCopyrightText: 2026 chenxya <chenxya@ghrah.org>

SPDX-License-Identifier: Apache-2.0
-->

<script setup lang="ts">
import type { AgentManifestInfo } from "@ghrah/observer-core";
import { onMounted, ref } from "vue";
import { useI18n } from "vue-i18n";
import AgentManifestCard from "@/components/config/agent-manifest-card.vue";
import AgentManifestDetail from "@/components/config/agent-manifest-detail.vue";
import EditAgentManifestDialog from "@/components/config/edit-agent-manifest-dialog.vue";
import NewAgentManifestDialog from "@/components/config/new-agent-manifest-dialog.vue";
import SpawnFromManifestDialog from "@/components/config/spawn-from-manifest-dialog.vue";
import ConfirmDialog from "@/components/ui/confirm-dialog.vue";
import { useObserver } from "@/composables/useObserver";
import UiButton from "@/components/ui/button.vue";

const { manifests, listManifestAgents, deleteAgent } = useObserver();
const { t } = useI18n();
const emit = defineEmits<{ dirtyChange: [dirty: boolean] }>();

const selected = ref<AgentManifestInfo | null>(null);
const showSpawnDialog = ref(false);
const showNewDialog = ref(false);
const loading = ref(false);
const error = ref<string | null>(null);
const deleteTarget = ref<string | null>(null);
const editTarget = ref<string | null>(null);
function closeEditDialog() {
  editTarget.value = null;
  setDialogDirty(false);
}
function onAgentSaved() {
  selected.value =
    manifests.agentList.find((agent) => agent.full_name === editTarget.value) ?? null;
}

function setDialogDirty(dirty: boolean) {
  emit("dirtyChange", dirty);
}

function closeSpawnDialog() {
  showSpawnDialog.value = false;
  setDialogDirty(false);
}

function closeNewDialog() {
  showNewDialog.value = false;
  setDialogDirty(false);
}

onMounted(async () => {
  try {
    const result = await listManifestAgents();
    if (!result) error.value = t("config.agent.notConnected");
    else if (!result.success) error.value = result.error || t("config.agent.loadFailed");
  } catch (cause) {
    error.value = cause instanceof Error ? cause.message : t("config.agent.loadFailed");
  }
});

function handleSelect(manifest: AgentManifestInfo) {
  selected.value = selected.value?.full_name === manifest.full_name ? null : manifest;
}

function handleSpawn(manifest: AgentManifestInfo) {
  selected.value = manifest;
  showSpawnDialog.value = true;
}

function handleEdit(manifest: AgentManifestInfo) {
  editTarget.value = manifest.full_name;
}

async function confirmDelete() {
  const fullName = deleteTarget.value;
  if (!fullName) return;
  deleteTarget.value = null;
  loading.value = true;
  error.value = null;
  const result = await deleteAgent(fullName);
  loading.value = false;
  if (result && !result.success) {
    error.value = result.error ?? t("config.agent.deleteFailed");
  } else if (result === null) {
    error.value = t("config.agent.notConnected");
  }
  if (selected.value?.full_name === fullName) {
    selected.value = null;
  }
}
</script>

<template>
  <section class="agent-config">
    <header class="agent-config-header">
      <h2>{{ t("config.agent.title") }}</h2>
      <UiButton variant="primary" @click="showNewDialog = true">
        {{ t("config.agent.newManifest") }}
      </UiButton>
    </header>

    <p v-if="error" class="agent-config-alert" role="alert">{{ error }}</p>

    <p v-if="manifests.agentsLoading" class="agent-config-state">
      {{ t("common.loading") }}
    </p>

    <p v-else-if="manifests.agentList.length === 0" class="agent-config-state">
      {{ t("config.agent.empty") }}
    </p>

    <ul v-else class="agent-config-list">
      <li v-for="m in manifests.agentList" :key="m.full_name">
        <AgentManifestCard
          :manifest="m"
          :selected="selected?.full_name === m.full_name"
          @select="handleSelect(m)"
          @spawn="handleSpawn(m)"
          @edit="handleEdit(m)"
          @delete="deleteTarget = m.full_name"
        />
      </li>
    </ul>

    <AgentManifestDetail
      :manifest="selected"
      @spawn="handleSpawn(selected!)"
      @edit="handleEdit(selected!)"
      @delete="deleteTarget = selected!.full_name"
    />

    <EditAgentManifestDialog
      v-if="editTarget"
      :key="editTarget"
      :full-name="editTarget"
      @dirty-change="setDialogDirty"
      @close="closeEditDialog"
      @saved="onAgentSaved"
    />
    <SpawnFromManifestDialog
      v-if="showSpawnDialog && selected"
      :manifest="selected"
      @dirty-change="setDialogDirty"
      @close="closeSpawnDialog"
    />

    <NewAgentManifestDialog
      v-if="showNewDialog"
      @dirty-change="setDialogDirty"
      @close="closeNewDialog"
    />

    <ConfirmDialog
      v-if="deleteTarget"
      :title="t('config.agent.deleteTitle')"
      :message="t('config.agent.deleteConfirm')"
      :confirm-label="t('config.agent.delete')"
      danger
      @cancel="deleteTarget = null"
      @confirm="confirmDelete"
    />
  </section>
</template>

<style scoped>
.agent-config {
  display: flex;
  min-width: 0;
  flex-direction: column;
  gap: 1rem;
  color: var(--color-text);
  font-size: var(--font-ui-body);
  overflow-wrap: anywhere;
}

.agent-config-header {
  display: flex;
  flex-wrap: wrap;
  align-items: center;
  justify-content: space-between;
  gap: 0.75rem 1rem;
}

.agent-config-header h2,
.agent-config-alert,
.agent-config-state {
  margin: 0;
}

.agent-config-header h2 {
  font-size: var(--font-ui-body);
  font-weight: 600;
}

.agent-config-alert,
.agent-config-state {
  padding: 0.75rem 1rem;
  border: 1px solid var(--color-border);
  border-radius: var(--radius-md);
  font-size: var(--font-ui-note);
  line-height: 1.5;
}

.agent-config-alert {
  border-color: var(--color-danger);
  background: color-mix(in srgb, var(--color-danger) 12%, var(--color-surface));
  color: var(--color-danger);
}

.agent-config-state {
  background: var(--color-surface);
  color: var(--color-text-muted);
  text-align: center;
}

.agent-config-list {
  display: grid;
  margin: 0;
  padding: 0;
  gap: 0.5rem;
  grid-template-columns: repeat(auto-fill, minmax(16rem, 1fr));
  list-style: none;
}
</style>
