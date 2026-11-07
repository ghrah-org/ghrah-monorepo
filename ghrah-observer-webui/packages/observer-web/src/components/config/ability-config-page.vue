<!--
SPDX-FileCopyrightText: 2026 chenxya <chenxya@ghrah.org>

SPDX-License-Identifier: Apache-2.0
-->

<script setup lang="ts">
import type { AbilityManifestInfo } from "@ghrah/observer-core";
import { nextTick, onMounted, onUnmounted, ref } from "vue";
import { useI18n } from "vue-i18n";
import { useObserver } from "@/composables/useObserver";
import ConfirmDialog from "@/components/ui/confirm-dialog.vue";
import NewAbilityManifest from "./new-ability-manifest.vue";

const {
  manifests,
  listManifestAbilities,
  getAbility,
  putAbility,
  deleteAbility,
  validateManifest,
} = useObserver();
const { t } = useI18n();
const loading = ref(false);
const error = ref("");
const legacyDisabled = ref(false);
const creating = ref(false);
const highlighted = ref("");
const abilityListElement = ref<HTMLElement | null>(null);
const deleteTarget = ref("");
const pageStatus = ref("");
const successToast = ref<HTMLElement | null>(null);
let statusTimer: ReturnType<typeof setTimeout> | undefined;
function dismissStatus() {
  pageStatus.value = "";
  clearTimeout(statusTimer);
}
function dismissStatusOutside(event: PointerEvent) {
  if (!successToast.value?.contains(event.target as Node)) dismissStatus();
}
function showDeleted(name: string) {
  dismissStatus();
  pageStatus.value = t("config.ability.deleted", { name });
  statusTimer = setTimeout(dismissStatus, 2000);
}
async function confirmDelete() {
  const name = deleteTarget.value;
  deleteTarget.value = "";
  busy.value = true;
  editorError.value = "";
  editorStatus.value = "";
  dismissStatus();
  try {
    const result = await deleteAbility(name);
    if (!result) throw new Error(t("config.ability.notConnected"));
    if (!result.success) throw new Error(result.error || t("config.ability.deleteFailed"));
    selected.value = "";
    detail.value = null;
    showDeleted(name);
    await refresh();
  } catch (cause) {
    editorError.value = cause instanceof Error ? cause.message : t("config.ability.deleteFailed");
  } finally {
    busy.value = false;
  }
}
function startCreating() {
  closeDetail();
  creating.value = true;
}
async function onCreated(fullName: string) {
  creating.value = false;
  await refresh();
  highlighted.value = fullName;
  await nextTick();
  const card = abilityListElement.value?.querySelector<HTMLElement>(
    ".ability-highlighted .ability-card",
  );
  card?.scrollIntoView({ behavior: "smooth", block: "center" });
  card?.focus({ preventScroll: true });
}
const permissionKeys = [
  "require_hitl",
  "fs_read_only",
  "fs_write",
  "net_access",
  "shell_access",
] as const;
function permissions(ability: AbilityManifestInfo) {
  return permissionKeys.filter((key) => ability.permissions?.[key]);
}
async function refresh() {
  if (loading.value) return;
  loading.value = true;
  error.value = "";
  try {
    const result = await listManifestAbilities();
    if (!result) error.value = t("config.ability.notConnected");
    else if (result.success) legacyDisabled.value = false;
    else if (result.error === "legacy_ability_manifest_disabled") {
      legacyDisabled.value = true;
      creating.value = false;
      editing.value = false;
      deleteTarget.value = "";
      dismissStatus();
      closeDetail();
    } else error.value = result.error || t("config.ability.loadFailed");
  } catch {
    error.value = t("config.ability.loadFailed");
  } finally {
    loading.value = false;
  }
}
interface AbilityDetail {
  metadata: Pick<
    AbilityManifestInfo,
    "namespace" | "name" | "title" | "description" | "permissions"
  >;
  implementation: { type: string };
}
const selected = ref("");
const detail = ref<AbilityDetail | null>(null);
const detailLoading = ref(false);
const detailError = ref("");
const source = ref("");
const draft = ref("");
const editing = ref(false);
const busy = ref(false);
const editorError = ref("");
const editorStatus = ref("");
function startEditing() {
  draft.value = source.value;
  editorError.value = "";
  editorStatus.value = "";
  editing.value = true;
}
function cancelEditing() {
  editing.value = false;
  draft.value = source.value;
  editorError.value = "";
  editorStatus.value = "";
}
async function submitEditor(save: boolean) {
  busy.value = true;
  editorError.value = "";
  editorStatus.value = "";
  try {
    const result = await validateManifest(draft.value, "ability");
    if (!result) throw new Error(t("config.ability.notConnected"));
    if (!result.success) throw new Error(result.error || t("config.ability.validationFailed"));
    const validation = result.data as { valid: boolean; errors: string[] };
    if (!validation.valid) {
      editorError.value = validation.errors.join("\n") || t("config.ability.validationFailed");
      return;
    }
    if (!save) {
      editorStatus.value = t("config.ability.valid");
      return;
    }
    const saved = await putAbility(selected.value, draft.value, true);
    if (!saved) throw new Error(t("config.ability.notConnected"));
    if (!saved.success) throw new Error(saved.error || t("config.ability.saveFailed"));
    const data = saved.data as { manifest: AbilityDetail; source: string };
    detail.value = data.manifest;
    source.value = data.source;
    editing.value = false;
    editorStatus.value = t("config.ability.saved");
    await refresh();
  } catch (cause) {
    editorError.value = cause instanceof Error ? cause.message : t("config.ability.saveFailed");
  } finally {
    busy.value = false;
  }
}
const ruleKeys = ["allowed_paths", "denied_paths", "allowed_commands", "denied_commands"] as const;
function closeDetail() {
  if (editing.value || busy.value) return;
  selected.value = "";
  detail.value = null;
  detailLoading.value = false;
  detailError.value = "";
}
function toggleAbility(name: string) {
  if (editing.value || busy.value || creating.value) return;
  highlighted.value = "";
  if (selected.value === name) closeDetail();
  else void selectAbility(name);
}
async function selectAbility(name: string) {
  editorStatus.value = "";
  editorError.value = "";
  selected.value = name;
  detail.value = null;
  detailError.value = "";
  detailLoading.value = true;
  try {
    const result = await getAbility(name);
    if (selected.value !== name) return;
    if (!result) throw new Error(t("config.ability.notConnected"));
    if (!result.success) throw new Error(result.error || t("config.ability.detailFailed"));
    const data = result.data as { manifest: AbilityDetail; source: string };
    detail.value = data.manifest;
    source.value = data.source;
  } catch (cause) {
    if (selected.value === name)
      detailError.value = cause instanceof Error ? cause.message : t("config.ability.detailFailed");
  } finally {
    if (selected.value === name) detailLoading.value = false;
  }
}
onMounted(refresh);
onMounted(() => document.addEventListener("pointerdown", dismissStatusOutside, true));
onUnmounted(() => {
  clearTimeout(statusTimer);
  document.removeEventListener("pointerdown", dismissStatusOutside, true);
});
</script>

<template>
  <section class="ability-page" :aria-busy="loading">
    <header class="ability-header">
      <div>
        <h2 class="text-lg font-semibold">{{ t("config.ability.title") }}</h2>
        <p class="ability-muted">
          {{ t(legacyDisabled ? "config.ability.legacyDisabled" : "config.ability.listHint") }}
        </p>
      </div>
      <div class="ability-permissions">
        <button
          v-if="!legacyDisabled"
          class="btn-primary"
          :disabled="loading || editing || busy || creating"
          @click="startCreating"
        >
          {{ t("config.ability.create") }}
        </button>
        <button
          class="btn-secondary"
          :disabled="loading || editing || busy || creating"
          @click="refresh"
        >
          {{ t("config.ability.refresh") }}
        </button>
      </div>
    </header>
    <NewAbilityManifest v-if="creating" @cancel="creating = false" @created="onCreated" />
    <p v-if="!legacyDisabled" class="ability-muted">
      {{ t("config.ability.registered") }} {{ manifests.abilityList.length }}
    </p>
    <p v-if="error" role="alert">{{ error }}</p>
    <div v-if="pageStatus" ref="successToast" class="ability-success-toast" role="status">
      <span class="ability-success-icon" aria-hidden="true">✓</span>
      <span>{{ pageStatus }}</span>
    </div>
    <p v-if="loading" role="status">{{ t("common.loading") }}</p>
    <p
      v-else-if="!legacyDisabled && !error && !manifests.abilityList.length"
      class="ability-muted"
      role="status"
    >
      {{ t("config.ability.empty") }}
    </p>
    <ul v-if="!legacyDisabled" ref="abilityListElement" class="ability-list">
      <li
        v-for="ability in manifests.abilityList"
        :key="ability.full_name"
        class="ability-slot"
        :class="{
          'ability-selected': selected === ability.full_name,
          'ability-highlighted': highlighted === ability.full_name,
        }"
      >
        <div
          class="ability-card ability-select"
          role="button"
          tabindex="0"
          :aria-expanded="selected === ability.full_name"
          @click="toggleAbility(ability.full_name)"
          @keydown.enter.prevent="toggleAbility(ability.full_name)"
          @keydown.space.prevent="toggleAbility(ability.full_name)"
          @keydown.esc.stop="closeDetail"
        >
          <div class="ability-header">
            <h3 class="font-semibold">{{ ability.title || ability.name || ability.full_name }}</h3>
            <span v-if="ability.namespace" class="ability-badge">{{ ability.namespace }}</span>
          </div>
          <code class="ability-muted">{{ ability.full_name }}</code>
          <p>{{ ability.description || t("config.ability.noDescription") }}</p>
          <div
            v-if="ability.permissions"
            class="ability-permissions"
            :aria-label="t('config.ability.permissions')"
          >
            <span v-for="key in permissions(ability)" :key="key" class="ability-badge">
              {{ t(`config.ability.permissionLabels.${key}`) }}
            </span>
            <span v-if="!permissions(ability).length" class="ability-muted">
              {{ t("config.ability.noPermissionFlags") }}
            </span>
          </div>
        </div>
        <section
          v-if="selected === ability.full_name"
          class="ability-detail"
          :aria-label="t('config.ability.details')"
          :aria-busy="detailLoading"
          @click="closeDetail"
          @keydown.esc.stop="closeDetail"
        >
          <p v-if="detailLoading" role="status">{{ t("common.loading") }}</p>
          <div v-else-if="detailError" role="alert">
            <p>{{ detailError }}</p>
            <button type="button" class="btn-secondary" @click.stop="selectAbility(selected)">
              {{ t("config.ability.refresh") }}
            </button>
          </div>
          <template v-else-if="detail">
            <div class="ability-editor" @click.stop @keydown.esc.stop>
              <template v-if="editing">
                <label for="ability-yaml" class="font-semibold">{{
                  t("config.ability.yaml")
                }}</label>
                <p class="ability-muted">{{ t("config.ability.editHint") }}</p>
                <textarea
                  id="ability-yaml"
                  v-model="draft"
                  class="ability-yaml"
                  rows="18"
                  spellcheck="false"
                  :disabled="busy"
                  @input="
                    editorError = '';
                    editorStatus = '';
                  "
                />
                <div class="ability-permissions">
                  <button class="btn-secondary" :disabled="busy" @click="submitEditor(false)">
                    {{ t("config.ability.validate") }}
                  </button>
                  <button class="btn-primary" :disabled="busy" @click="submitEditor(true)">
                    {{ t("config.ability.save") }}
                  </button>
                  <button class="btn-secondary" :disabled="busy" @click="cancelEditing">
                    {{ t("config.ability.cancel") }}
                  </button>
                </div>
              </template>
              <div v-else class="ability-permissions">
                <button class="btn-secondary" :disabled="busy" @click="startEditing">
                  {{ t("config.ability.edit") }}
                </button>
                <button class="btn-secondary" :disabled="busy" @click="deleteTarget = selected">
                  {{ t("config.ability.delete") }}
                </button>
              </div>
              <p v-if="busy" role="status">{{ t("common.loading") }}</p>
              <p v-if="editorError" class="ability-editor-message" role="alert">
                {{ editorError }}
              </p>
              <p v-if="editorStatus" role="status">{{ editorStatus }}</p>
            </div>
            <template v-if="!editing">
              <p>
                {{ t("config.ability.implementation") }}:
                <code>{{ detail.implementation.type }}</code>
              </p>
              <h4 class="font-semibold">{{ t("config.ability.permissions") }}</h4>
              <dl class="ability-rules">
                <div v-for="key in permissionKeys" :key="key">
                  <dt>{{ t(`config.ability.permissionLabels.${key}`) }}</dt>
                  <dd>
                    {{
                      t(
                        detail.metadata.permissions[key]
                          ? "config.ability.enabled"
                          : "config.ability.disabled",
                      )
                    }}
                  </dd>
                </div>
                <div v-for="key in ruleKeys" :key="key">
                  <dt>{{ t(`config.ability.rules.${key}`) }}</dt>
                  <dd>
                    <ul v-if="detail.metadata.permissions[key]?.length">
                      <li v-for="(entry, index) in detail.metadata.permissions[key]" :key="index">
                        <code>{{ entry }}</code>
                      </li>
                    </ul>
                    <span v-else>{{ t("config.ability.notSpecified") }}</span>
                  </dd>
                </div>
              </dl>
            </template>
          </template>
        </section>
      </li>
    </ul>
  </section>
  <ConfirmDialog
    v-if="deleteTarget"
    :title="t('config.ability.deleteTitle')"
    :message="t('config.ability.deleteConfirm', { name: deleteTarget })"
    :confirm-label="t('config.ability.delete')"
    :cancel-label="t('config.ability.cancel')"
    danger
    @cancel="deleteTarget = ''"
    @confirm="confirmDelete"
  />
</template>

<style scoped>
.ability-success-toast {
  position: fixed;
  top: 50%;
  left: 50%;
  transform: translate(-50%, -50%);
  z-index: 1000;
  display: flex;
  flex-direction: column;
  align-items: center;
  justify-content: center;
  gap: var(--spacing-4);
  width: 50vw;
  min-height: calc(var(--spacing-4) * 12);
  max-width: calc(100vw - var(--spacing-4) * 4);
  box-sizing: border-box;
  padding: calc(var(--spacing-4) * 2) calc(var(--spacing-4) * 3);
  border-radius: calc(var(--radius-md) * 3);
  background: linear-gradient(
    135deg,
    color-mix(in srgb, var(--ac-badge-head) 14%, var(--ac-node-bg)),
    color-mix(in srgb, var(--ac-converse) 10%, var(--ac-node-bg))
  );
  color: var(--ac-badge-head);
  box-shadow: 0 var(--spacing-4) calc(var(--spacing-4) * 3) var(--ac-node-border);
  font-size: calc(var(--font-ui-body) * 1.5);
  font-weight: 600;
  text-align: center;
  overflow-wrap: anywhere;
}
.ability-success-icon {
  flex-shrink: 0;
  font-size: calc(var(--font-ui-body) * 3);
  font-weight: bold;
}
.ability-editor {
  display: flex;
  flex-direction: column;
  align-items: flex-start;
  gap: var(--spacing-3);
  cursor: auto;
}
.ability-yaml {
  width: 100%;
  box-sizing: border-box;
  padding: var(--spacing-3);
  border: 1px solid var(--ac-node-border);
  border-radius: var(--radius-md);
  background: var(--ac-node-bg);
  color: inherit;
  font-family: ui-monospace, SFMono-Regular, Menlo, monospace;
  font-size: var(--font-ui-note);
  resize: vertical;
}
.ability-yaml:focus-visible {
  outline: 2px solid var(--ac-read);
}
.ability-editor-message {
  white-space: pre-wrap;
}
.ability-page {
  display: flex;
  flex-direction: column;
  gap: var(--spacing-4);
  height: 100%;
  overflow-y: auto;
  font-size: var(--font-ui-body);
}
.ability-header {
  display: flex;
  align-items: center;
  justify-content: space-between;
  gap: var(--spacing-3);
}
.ability-list {
  display: grid;
  grid-template-columns: repeat(2, minmax(0, 1fr));
  gap: var(--spacing-3);
  list-style: none;
  padding: 0;
  margin: 0;
}
.ability-card {
  display: flex;
  flex-direction: column;
  gap: var(--spacing-2);
  padding: var(--spacing-4);
  border: 1px solid var(--ac-node-border);
  border-radius: var(--radius-md);
  background: var(--ac-node-bg);
  overflow-wrap: anywhere;
}
.ability-muted {
  color: var(--ac-node-subtext);
  font-size: var(--font-ui-note);
}
.ability-permissions {
  display: flex;
  flex-wrap: wrap;
  gap: var(--spacing-2);
}
.ability-badge {
  font-size: var(--font-ui-note);
  color: var(--ac-read);
  border: 1px solid var(--ac-node-border);
  border-radius: var(--radius-md);
  padding-inline: var(--spacing-2);
}
.ability-select {
  color: inherit;
  font: inherit;
  text-align: left;
  cursor: pointer;
}
.ability-select:hover {
  border-color: var(--ac-read);
}
.ability-select:focus-visible {
  outline: 2px solid var(--ac-read);
}
.ability-slot {
  position: relative;
  min-width: 0;
}
.ability-slot > .ability-card {
  height: 100%;
  box-sizing: border-box;
}
.ability-selected {
  z-index: 2;
  filter: drop-shadow(0 var(--spacing-2) var(--spacing-3) var(--ac-node-border));
}
.ability-selected > .ability-card {
  border-color: var(--ac-read);
  border-bottom-color: transparent;
  border-bottom-left-radius: 0;
  border-bottom-right-radius: 0;
}
.ability-highlighted > .ability-card {
  border-color: var(--ac-read);
  box-shadow: 0 var(--spacing-2) var(--spacing-4) var(--ac-node-border);
}
.ability-detail {
  position: absolute;
  top: calc(100% - 1px);
  left: 0;
  right: 0;
  display: flex;
  flex-direction: column;
  gap: var(--spacing-3);
  padding: var(--spacing-4);
  background: var(--ac-node-bg);
  border: 1px solid var(--ac-read);
  border-top: 0;
  border-radius: 0 0 var(--radius-md) var(--radius-md);
  cursor: pointer;
  overflow-wrap: anywhere;
}
.ability-rules {
  display: grid;
  grid-template-columns: repeat(2, minmax(0, 1fr));
  gap: var(--spacing-3);
}
.ability-rules dd {
  margin: 0;
}
.ability-rules dt {
  color: var(--ac-node-subtext);
}
</style>
