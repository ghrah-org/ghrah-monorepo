<script setup lang="ts">
import { extractManifestEntry, type ManifestEntry } from "@ghrah/observer-core";
import { computed, onBeforeUnmount, onMounted, ref, shallowRef, watch } from "vue";
import { useI18n } from "vue-i18n";
import AbilityManifestDetail from "@/components/config/ability-manifest-detail.vue";
import AbilityManifestEditorDialog from "@/components/config/ability-manifest-editor-dialog.vue";
import ConfirmDialog from "@/components/ui/confirm-dialog.vue";
import { useObserver } from "@/composables/useObserver";

const { manifests, connection, listManifestAbilities, getAbility, deleteAbility } = useObserver();
const { t } = useI18n();
const emit = defineEmits<{ dirtyChange: [dirty: boolean] }>();
const connected = computed(() => connection.state === "connected");
const selectedFullName = ref<string | null>(null);
const selected = shallowRef<Extract<ManifestEntry, { kind: "ability" }> | null>(null);
const editor = ref<{ fullName?: string; mode: "view" | "edit" } | null>(null);
const loading = ref(false);
const detailLoading = ref(false);
const deleting = ref(false);
const loadError = ref("");
const detailError = ref("");
const deleteError = ref("");
const deleteTarget = ref<string | null>(null);
let listRequest = 0;
let detailRequest = 0;
let active = true;

function clearDetail() {
  detailRequest++;
  selected.value = null;
  detailError.value = "";
  detailLoading.value = false;
}

async function loadDetail(fullName: string) {
  clearDetail();
  const request = detailRequest;
  detailLoading.value = true;
  try {
    const result = await getAbility(fullName);
    if (!active || request !== detailRequest || selectedFullName.value !== fullName) return;
    if (!result?.success) {
      detailError.value = result?.error ?? t("config.ability.detailFailed");
      return;
    }
    const entry = extractManifestEntry(result.data, { kind: "ability", fullName });
    if (entry?.kind !== "ability") {
      detailError.value = t("common.invalidResponse");
      return;
    }
    selected.value = entry;
  } catch (error) {
    if (active && request === detailRequest) {
      detailError.value = error instanceof Error ? error.message : t("config.ability.detailFailed");
    }
  } finally {
    if (active && request === detailRequest) detailLoading.value = false;
  }
}

async function refresh() {
  const request = ++listRequest;
  clearDetail();
  loading.value = true;
  loadError.value = "";
  try {
    const result = await listManifestAbilities();
    if (!active || request !== listRequest) return;
    if (!result?.success) {
      loadError.value = result?.error ?? t("config.ability.loadFailed");
      return;
    }
    const fullName = selectedFullName.value;
    if (fullName && manifests.abilityList.some((item) => item.full_name === fullName)) {
      await loadDetail(fullName);
    } else if (fullName) {
      selectedFullName.value = null;
      clearDetail();
    }
  } catch (error) {
    if (active && request === listRequest) {
      loadError.value = error instanceof Error ? error.message : t("config.ability.loadFailed");
    }
  } finally {
    if (active && request === listRequest) loading.value = false;
  }
}

function selectManifest(fullName: string) {
  selectedFullName.value = fullName;
  deleteError.value = "";
  void loadDetail(fullName);
}

function openEditor(mode: "view" | "edit") {
  if (selectedFullName.value) editor.value = { fullName: selectedFullName.value, mode };
}

function closeEditor() {
  editor.value = null;
  emit("dirtyChange", false);
}

async function handleSaved(fullName: string) {
  selectedFullName.value = fullName;
  await refresh();
}

async function confirmDelete() {
  const fullName = deleteTarget.value;
  if (!fullName || deleting.value || !connected.value) return;
  deleteTarget.value = null;
  deleting.value = true;
  deleteError.value = "";
  try {
    const result = await deleteAbility(fullName);
    if (!active) return;
    if (!result?.success) {
      deleteError.value = result?.error ?? t("config.ability.deleteFailed");
      return;
    }
    if (selectedFullName.value === fullName) {
      selectedFullName.value = null;
      clearDetail();
    }
    await refresh();
  } catch (error) {
    if (active) {
      deleteError.value = error instanceof Error ? error.message : t("config.ability.deleteFailed");
    }
  } finally {
    if (active) deleting.value = false;
  }
}

onMounted(() => void refresh());
watch(
  () => connection.state,
  (state) => {
    if (state === "connected") void refresh();
    else {
      listRequest++;
      loading.value = false;
      clearDetail();
      deleteTarget.value = null;
    }
  },
);
onBeforeUnmount(() => {
  active = false;
  listRequest++;
  detailRequest++;
});
</script>

<template>
  <div class="ability-config-page">
    <header class="ability-page-header">
      <h2>{{ t("config.ability.title") }}</h2>
      <div class="ability-actions">
        <button
          class="btn-secondary"
          :disabled="!connected || loading || deleting"
          @click="refresh"
        >
          {{ t("config.ability.refresh") }}
        </button>
        <button
          class="btn-primary"
          :disabled="!connected || deleting"
          @click="editor = { mode: 'edit' }"
        >
          {{ t("config.ability.newManifest") }}
        </button>
      </div>
    </header>
    <p>{{ t("config.ability.scopeHint") }}</p>
    <p>{{ t("config.ability.fileHint") }}</p>
    <p v-if="!connected" role="alert">{{ t("config.manifest.notConnected") }}</p>
    <p v-if="loadError" role="alert">{{ loadError }}</p>
    <p v-if="deleteError" role="alert">{{ deleteError }}</p>
    <div v-if="manifests.abilityErrors.length" class="ability-errors" role="alert">
      <h3>{{ t("config.ability.listErrors") }}</h3>
      <p v-for="item in manifests.abilityErrors" :key="item.full_name">
        <code>{{ item.full_name }}</code
        >: {{ item.error }}
      </p>
    </div>
    <p v-if="loading" role="status">{{ t("common.loading") }}</p>
    <p v-else-if="!loadError && !manifests.abilityList.length">{{ t("config.ability.empty") }}</p>
    <ul
      v-if="manifests.abilityList.length"
      class="ability-list"
      :aria-label="t('config.ability.list')"
    >
      <li v-for="manifest in manifests.abilityList" :key="manifest.full_name">
        <button
          class="ability-list-item"
          :aria-pressed="selectedFullName === manifest.full_name"
          :disabled="!connected || deleting"
          @click="selectManifest(manifest.full_name)"
        >
          <strong>{{ manifest.title || manifest.name }}</strong>
          <code>{{ manifest.full_name }}</code>
          <span v-if="manifest.description">{{ manifest.description }}</span>
          <span>{{
            t("config.ability.implementationType", { type: manifest.implementation_type })
          }}</span>
          <span v-if="manifest.has_hitl">{{ t("config.ability.requiresApproval") }}</span>
          <span v-if="manifest.tags.length">{{ manifest.tags.join(", ") }}</span>
        </button>
      </li>
    </ul>
    <p v-if="detailLoading" role="status">{{ t("config.ability.loadingDetail") }}</p>
    <div v-else-if="detailError" role="alert">
      <p>{{ detailError }}</p>
      <button
        class="btn-secondary"
        :disabled="!connected || !selectedFullName"
        @click="selectedFullName && loadDetail(selectedFullName)"
      >
        {{ t("config.ability.retryDetail") }}
      </button>
    </div>
    <AbilityManifestDetail
      v-else-if="selected"
      :detail="selected.detail"
      :disabled="!connected || deleting || loading"
      @edit="openEditor('edit')"
      @view="openEditor('view')"
      @delete="deleteTarget = selectedFullName"
    />
    <p v-else-if="!loading && !loadError">{{ t("config.ability.selectManifest") }}</p>
    <AbilityManifestEditorDialog
      v-if="editor"
      :key="editor.fullName ?? 'create'"
      :full-name="editor.fullName"
      :initial-mode="editor.mode"
      @dirty-change="emit('dirtyChange', $event)"
      @saved="handleSaved"
      @close="closeEditor"
    />
    <ConfirmDialog
      v-if="deleteTarget"
      :title="t('config.ability.deleteTitle')"
      :message="t('config.ability.deleteConfirm', { name: deleteTarget })"
      :confirm-label="t('config.ability.delete')"
      danger
      @cancel="deleteTarget = null"
      @confirm="confirmDelete"
    />
  </div>
</template>

<style scoped>
.ability-config-page {
  display: grid;
  align-content: start;
  gap: var(--spacing-3);
  min-width: 0;
  font-size: var(--font-ui-body);
}
.ability-page-header,
.ability-actions {
  display: flex;
  align-items: center;
  justify-content: space-between;
  flex-wrap: wrap;
  gap: var(--spacing-2);
}
.ability-config-page h2,
.ability-config-page h3,
.ability-config-page p {
  margin: 0;
  font-size: inherit;
  overflow-wrap: anywhere;
}
.ability-list {
  display: grid;
  grid-template-columns: repeat(auto-fit, minmax(min(100%, 18rem), 1fr));
  gap: var(--spacing-2);
  margin: 0;
  padding: 0;
  list-style: none;
}
.ability-list-item {
  display: grid;
  gap: var(--spacing-2);
  width: 100%;
  height: 100%;
  padding: var(--spacing-3);
  border: 1px solid var(--ac-node-border);
  border-radius: var(--radius-md);
  background: var(--ac-node-bg);
  color: var(--ac-node-text);
  text-align: start;
  cursor: pointer;
  overflow-wrap: anywhere;
}
.ability-list-item[aria-pressed="true"] {
  border-color: var(--ac-read);
  box-shadow: inset 0 0 0 1px var(--ac-read);
}
.ability-list-item:disabled {
  cursor: default;
}
.ability-list-item code {
  font-size: var(--font-ui-code);
}
.ability-errors {
  display: grid;
  gap: var(--spacing-2);
}
</style>
