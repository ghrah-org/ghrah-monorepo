<!--
SPDX-FileCopyrightText: 2026 chenxya <chenxya@ghrah.org>

SPDX-License-Identifier: Apache-2.0
-->

<script setup lang="ts">
import { computed, nextTick, onMounted, onUnmounted, ref } from "vue";
import { useI18n } from "vue-i18n";
import { useObserver } from "@/composables/useObserver";

const STORAGE_KEY = "ghrah.instance-profile.display-name";
const emit = defineEmits<{ openSettings: [] }>();
const { t } = useI18n();
const { connection, autoConnect, disconnect } = useObserver();
const root = ref<HTMLElement | null>(null);
const nameInput = ref<HTMLInputElement | null>(null);
const addressButton = ref<HTMLButtonElement | null>(null);
const connectionPopover = ref<HTMLElement | null>(null);
const connectionAction = ref<HTMLButtonElement | null>(null);
const displayName = ref(loadDisplayName());
const draftName = ref(displayName.value);
const editing = ref(false);
const detailsOpen = ref(false);

const avatarLabel = computed(() => displayName.value.trim().slice(0, 1).toUpperCase() || "G");
const statusDotClass = computed(
  () =>
    ({
      connected: "instance-status-connected",
      connecting: "instance-status-pending",
      reconnecting: "instance-status-pending",
      disconnected: "instance-status-disconnected",
    })[connection.state],
);
const connectionBusy = computed(
  () => connection.state === "connecting" || connection.state === "reconnecting",
);

function loadDisplayName(): string {
  try {
    return localStorage.getItem(STORAGE_KEY)?.trim() || t("instanceProfile.defaultName");
  } catch {
    return t("instanceProfile.defaultName");
  }
}

async function startEditing() {
  draftName.value = displayName.value;
  editing.value = true;
  await nextTick();
  nameInput.value?.select();
}

function saveName() {
  const nextName = draftName.value.trim() || t("instanceProfile.defaultName");
  displayName.value = nextName;
  draftName.value = nextName;
  editing.value = false;
  try {
    localStorage.setItem(STORAGE_KEY, nextName);
  } catch {
    // 嵌入或隐私上下文可能禁用存储；内存中的名称仍然可用。
  }
}

function cancelEditing() {
  draftName.value = displayName.value;
  editing.value = false;
}

function openSettings() {
  void closeDetails(false);
  emit("openSettings");
}

async function toggleDetails() {
  if (detailsOpen.value) return closeDetails(true);
  detailsOpen.value = true;
  await nextTick();
  if (connectionAction.value && !connectionAction.value.disabled) connectionAction.value.focus();
  else connectionPopover.value?.focus();
}

async function closeDetails(restoreFocus: boolean) {
  if (!detailsOpen.value) return;
  detailsOpen.value = false;
  if (restoreFocus) {
    await nextTick();
    addressButton.value?.focus();
  }
}

function popoverFocusables(): HTMLElement[] {
  if (!connectionPopover.value) return [];
  return Array.from(
    connectionPopover.value.querySelectorAll<HTMLElement>(
      'button:not([disabled]), a[href], input:not([disabled]), select:not([disabled]), textarea:not([disabled]), [tabindex]:not([tabindex="-1"])',
    ),
  );
}

function onPopoverKeydown(event: KeyboardEvent) {
  if (event.key === "Escape") {
    event.preventDefault();
    event.stopPropagation();
    void closeDetails(true);
    return;
  }
  if (event.key !== "Tab") return;
  const items = popoverFocusables();
  const first = items[0];
  const last = items.at(-1);
  if (items.length === 0) {
    event.preventDefault();
    connectionPopover.value?.focus();
  } else if (
    (event.shiftKey &&
      (document.activeElement === first || document.activeElement === connectionPopover.value)) ||
    (!event.shiftKey && document.activeElement === last)
  ) {
    event.preventDefault();
    (event.shiftKey ? last : first)?.focus();
  }
}

function onWindowPointerDown(event: PointerEvent) {
  if (!root.value?.contains(event.target as Node)) void closeDetails(false);
}

onMounted(() => window.addEventListener("pointerdown", onWindowPointerDown));
onUnmounted(() => window.removeEventListener("pointerdown", onWindowPointerDown));
</script>

<template>
  <div ref="root" class="instance-profile-control">
    <div
      v-if="detailsOpen"
      ref="connectionPopover"
      class="instance-connection-popover"
      role="dialog"
      tabindex="-1"
      :aria-label="t('instanceProfile.connectionDetails')"
      @keydown="onPopoverKeydown"
    >
      <div class="instance-connection-heading">
        <span :class="['instance-status-dot', statusDotClass]" />
        <strong>{{ t(`app.status.${connection.state}`) }}</strong>
      </div>
      <code :title="connection.serverUrl">{{ connection.serverUrl }}</code>
      <button
        v-if="connection.state === 'connected'"
        ref="connectionAction"
        type="button"
        class="btn-secondary instance-connection-action"
        @click="disconnect"
      >
        {{ t("app.disconnect") }}
      </button>
      <button
        v-else
        ref="connectionAction"
        type="button"
        class="btn-primary instance-connection-action"
        :disabled="connectionBusy"
        @click="autoConnect()"
      >
        {{ t("app.connect") }}
      </button>
    </div>

    <span class="instance-avatar" aria-hidden="true">{{ avatarLabel }}</span>
    <div class="instance-profile-copy">
      <input
        v-if="editing"
        ref="nameInput"
        v-model="draftName"
        class="instance-name-input"
        :aria-label="t('instanceProfile.name')"
        @blur="saveName"
        @keydown.enter.prevent="saveName"
        @keydown.esc.prevent="cancelEditing"
      />
      <button
        v-else
        type="button"
        class="instance-display-name"
        :title="t('instanceProfile.editName')"
        @click="startEditing"
      >
        {{ displayName }}
      </button>
      <button
        ref="addressButton"
        type="button"
        class="instance-address"
        :aria-expanded="detailsOpen"
        :title="t('instanceProfile.connectionDetails')"
        @click="toggleDetails"
      >
        <span :class="['instance-status-dot', statusDotClass]" />
        <span>{{ connection.serverUrl }}</span>
      </button>
    </div>
    <button
      type="button"
      class="instance-settings-button"
      :title="t('instanceProfile.settings')"
      :aria-label="t('instanceProfile.settings')"
      @click="openSettings"
    >
      <span aria-hidden="true">⚙</span>
    </button>
  </div>
</template>
