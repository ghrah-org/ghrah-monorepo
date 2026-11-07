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
  <div class="flex flex-col h-full gap-4">
    <div class="flex items-center justify-between">
      <h2 class="text-lg font-semibold">{{ t("config.agent.title") }}</h2>
      <UiButton variant="primary" @click="showNewDialog = true">
        {{ t("config.agent.newManifest") }}
      </UiButton>
    </div>

    <div v-if="error" class="text-red-600 dark:text-red-400 text-sm">{{ error }}</div>

    <div v-if="manifests.agentsLoading" class="text-gray-400 text-sm py-4 text-center">
      {{ t("common.loading") }}
    </div>

    <div
      v-else-if="manifests.agentList.length === 0"
      class="text-gray-400 text-sm py-4 text-center italic"
    >
      {{ t("config.agent.empty") }}
    </div>

    <div v-else class="grid grid-cols-2 gap-2">
      <AgentManifestCard
        v-for="m in manifests.agentList"
        :key="m.full_name"
        :manifest="m"
        :selected="selected?.full_name === m.full_name"
        @select="handleSelect(m)"
        @spawn="handleSpawn(m)"
        @edit="handleEdit(m)"
        @delete="deleteTarget = m.full_name"
      />
    </div>

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
  </div>
</template>
