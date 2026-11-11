<!--
SPDX-FileCopyrightText: 2026 chenxya <chenxya@ghrah.org>

SPDX-License-Identifier: Apache-2.0
-->

<script setup lang="ts">
import { computed, nextTick, onMounted, onUnmounted, ref } from "vue";
import { useI18n } from "vue-i18n";
import { useObserver } from "@/composables/useObserver";
import UiButton from "@/components/ui/button.vue";

type SettingsSection =
  "general" | "agents" | "abilities" | "connections" | "plugins" | "tasks" | "archived";

const STORAGE_KEY = "ghrah.instance-profile.display-name";
const emit = defineEmits<{ openSettings: [section: SettingsSection] }>();
const { t } = useI18n();
const { connection, autoConnect, disconnect } = useObserver();
const root = ref<HTMLElement | null>(null);
const nameInput = ref<HTMLInputElement | null>(null);
const addressButton = ref<HTMLButtonElement | null>(null);
const connectionPopover = ref<HTMLElement | null>(null);
const connectionAction = ref<{ focus: () => void; readonly disabled: boolean } | null>(null);
const avatarButton = ref<HTMLButtonElement | null>(null);
const menuTrigger = ref<HTMLButtonElement | null>(null);
const instanceMenu = ref<HTMLElement | null>(null);
const displayName = ref(loadDisplayName());
const draftName = ref(displayName.value);
const editing = ref(false);
const detailsOpen = ref(false);
const menuOpen = ref(false);
let menuOrigin: HTMLElement | null = null;

// 默认头像先用九夏字标占位；品牌图形定稿后替换为 logo 资源。
// 名字自定义与头像自定义都属于后续「单用户个性化配置」feat，本轮只落默认身份。
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
const menuItems: Array<{
  id: string;
  section: SettingsSection;
  glyph: string;
  labelKey: string;
}> = [
  { id: "user", section: "general", glyph: "◉", labelKey: "instanceMenu.user" },
  { id: "agents", section: "agents", glyph: "◆", labelKey: "instanceMenu.agents" },
  { id: "abilities", section: "abilities", glyph: "◇", labelKey: "instanceMenu.abilities" },
  { id: "connections", section: "connections", glyph: "⇄", labelKey: "instanceMenu.connections" },
  { id: "system", section: "general", glyph: "⚙", labelKey: "instanceMenu.system" },
];

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

function menuItemElements(): HTMLButtonElement[] {
  if (!instanceMenu.value) return [];
  return Array.from(instanceMenu.value.querySelectorAll<HTMLButtonElement>('[role="menuitem"]'));
}

function focusMenuItem(index: number) {
  const items = menuItemElements();
  if (items.length === 0) return;
  items[(index + items.length) % items.length]?.focus();
}

async function openMenu(origin: HTMLElement | null) {
  menuOrigin = origin;
  if (detailsOpen.value) await closeDetails(false);
  menuOpen.value = true;
  await nextTick();
  focusMenuItem(0);
}

async function toggleMenu(origin: HTMLElement | null) {
  if (menuOpen.value) await closeMenu(true);
  else await openMenu(origin);
}

async function closeMenu(restoreFocus: boolean) {
  if (!menuOpen.value) return;
  menuOpen.value = false;
  if (restoreFocus) {
    const origin = menuOrigin;
    await nextTick();
    origin?.focus();
  }
}

function selectMenuSection(section: SettingsSection) {
  const origin = menuOrigin;
  menuOpen.value = false;
  // 设置页会记住打开时的焦点元素；先把焦点还给菜单触发器，关闭后才能回到原位。
  origin?.focus();
  emit("openSettings", section);
}

function onMenuKeydown(event: KeyboardEvent) {
  if (event.key === "Escape") {
    event.preventDefault();
    event.stopPropagation();
    void closeMenu(true);
    return;
  }
  const items = menuItemElements();
  if (items.length === 0) return;
  const current = items.indexOf(document.activeElement as HTMLButtonElement);
  if (event.key === "Tab") {
    // 菜单内自成一个焦点环，避免 Tab 落到被菜单遮住的底部按钮上。
    event.preventDefault();
    focusMenuItem(current < 0 ? 0 : event.shiftKey ? current - 1 : current + 1);
  } else if (event.key === "ArrowDown") {
    event.preventDefault();
    focusMenuItem(current < 0 ? 0 : current + 1);
  } else if (event.key === "ArrowUp") {
    event.preventDefault();
    focusMenuItem(current < 0 ? items.length - 1 : current - 1);
  } else if (event.key === "Home") {
    event.preventDefault();
    focusMenuItem(0);
  } else if (event.key === "End") {
    event.preventDefault();
    focusMenuItem(items.length - 1);
  }
}

async function toggleDetails() {
  if (detailsOpen.value) return closeDetails(true);
  if (menuOpen.value) await closeMenu(false);
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
  if (!root.value?.contains(event.target as Node)) {
    void closeDetails(false);
    void closeMenu(false);
  }
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
      <UiButton
        v-if="connection.state === 'connected'"
        ref="connectionAction"
        type="button"
        class="instance-connection-action"
        variant="secondary"
        @click="disconnect"
      >
        {{ t("app.disconnect") }}
      </UiButton>
      <UiButton
        v-else
        ref="connectionAction"
        type="button"
        class="instance-connection-action"
        variant="primary"
        :disabled="connectionBusy"
        @click="autoConnect()"
      >
        {{ t("app.connect") }}
      </UiButton>
    </div>

    <div v-if="menuOpen" id="instance-menu" ref="instanceMenu" class="instance-menu">
      <div class="instance-menu-header">
        <span class="instance-avatar instance-menu-avatar" aria-hidden="true">
          {{ t("instanceProfile.defaultAvatar") }}
        </span>
        <div class="instance-menu-identity">
          <strong class="instance-menu-name">{{ displayName }}</strong>
          <span class="instance-menu-status">
            <span :class="['instance-status-dot', statusDotClass]" />
            <span>{{ t(`app.status.${connection.state}`) }}</span>
            <code :title="connection.serverUrl">{{ connection.serverUrl }}</code>
          </span>
        </div>
      </div>
      <div
        class="instance-menu-list"
        role="menu"
        :aria-label="t('instanceMenu.label')"
        @keydown="onMenuKeydown"
      >
        <template v-for="item in menuItems" :key="item.id">
          <span v-if="item.id === 'system'" class="instance-menu-separator" role="separator" />
          <button
            type="button"
            role="menuitem"
            tabindex="-1"
            class="instance-menu-item"
            @click="selectMenuSection(item.section)"
          >
            <span class="instance-menu-glyph" aria-hidden="true">{{ item.glyph }}</span>
            <span class="instance-menu-label">{{ t(item.labelKey) }}</span>
          </button>
        </template>
      </div>
    </div>

    <button
      ref="avatarButton"
      type="button"
      class="instance-avatar-button"
      :title="t('instanceMenu.open')"
      :aria-label="t('instanceMenu.open')"
      aria-haspopup="menu"
      :aria-expanded="menuOpen ? 'true' : 'false'"
      :aria-controls="menuOpen ? 'instance-menu' : undefined"
      @click="toggleMenu(avatarButton)"
    >
      <span class="instance-avatar" aria-hidden="true">
        {{ t("instanceProfile.defaultAvatar") }}
        <span :class="['instance-avatar-status', statusDotClass]" />
      </span>
    </button>
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
      ref="menuTrigger"
      type="button"
      class="instance-menu-trigger"
      :title="t('instanceMenu.open')"
      :aria-label="t('instanceMenu.open')"
      aria-haspopup="menu"
      :aria-expanded="menuOpen ? 'true' : 'false'"
      :aria-controls="menuOpen ? 'instance-menu' : undefined"
      @click="toggleMenu(menuTrigger)"
    >
      <span aria-hidden="true">⚙</span>
    </button>
  </div>
</template>
