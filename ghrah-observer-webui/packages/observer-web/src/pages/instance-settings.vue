<!--
SPDX-FileCopyrightText: 2026 chenxya <chenxya@ghrah.org>

SPDX-License-Identifier: Apache-2.0
-->

<script setup lang="ts">
import { computed, nextTick, onMounted, onUnmounted, ref, watch } from "vue";
import { useI18n } from "vue-i18n";
import AbilityConfigPage from "@/components/config/ability-config-page.vue";
import AgentConfigPage from "@/components/config/agent-config-page.vue";
import ArchivedResourcesPage from "@/components/config/archived-resources-page.vue";
import ConfigNav from "@/components/config/config-nav.vue";
import ConnectionsConfigPage from "@/components/config/connections-config-page.vue";
import GeneralConfigPage from "@/components/config/general-config-page.vue";
import PluginConfigPage from "@/components/config/plugin-config-page.vue";
import TaskAttributionPage from "@/components/config/task-attribution-page.vue";
import ConfirmDialog from "@/components/ui/confirm-dialog.vue";

type Section =
  "general" | "agents" | "abilities" | "plugins" | "tasks" | "archived" | "connections";
const props = defineProps<{ initialSection?: Section }>();
const emit = defineEmits<{ close: [] }>();
const { t } = useI18n();
const activeSection = ref<Section>(props.initialSection ?? "general");
const dirty = ref(false);
const closeButton = ref<HTMLButtonElement | null>(null);
const pendingAction = ref<{ kind: "close" } | { kind: "section"; section: Section } | null>(null);

const activePage = computed(() => {
  if (activeSection.value === "agents") return AgentConfigPage;
  if (activeSection.value === "abilities") return AbilityConfigPage;
  if (activeSection.value === "connections") return ConnectionsConfigPage;
  if (activeSection.value === "plugins") return PluginConfigPage;
  if (activeSection.value === "tasks") return TaskAttributionPage;
  if (activeSection.value === "archived") return ArchivedResourcesPage;
  return GeneralConfigPage;
});

watch(
  () => props.initialSection,
  (section) => {
    if (section) activeSection.value = section;
  },
);

function selectSection(section: Section) {
  if (section === activeSection.value) return;
  if (dirty.value) {
    pendingAction.value = { kind: "section", section };
    return;
  }
  activeSection.value = section;
}

function requestClose() {
  if (dirty.value) pendingAction.value = { kind: "close" };
  else emit("close");
}

function confirmDiscard() {
  const action = pendingAction.value;
  pendingAction.value = null;
  dirty.value = false;
  if (action?.kind === "section") activeSection.value = action.section;
  else if (action?.kind === "close") emit("close");
}

function onKeydown(event: KeyboardEvent) {
  if (event.key !== "Escape" || event.defaultPrevented) return;
  requestClose();
}

onMounted(async () => {
  window.addEventListener("keydown", onKeydown);
  await nextTick();
  closeButton.value?.focus();
});
onUnmounted(() => window.removeEventListener("keydown", onKeydown));
</script>

<template>
  <section
    class="instance-settings"
    role="dialog"
    aria-modal="true"
    :aria-label="t('config.title')"
  >
    <aside class="instance-settings-sidebar">
      <div class="instance-settings-heading">
        <span class="brand-mark" aria-hidden="true">G</span>
        <div>
          <span>{{ t("app.title") }}</span
          ><strong>{{ t("config.title") }}</strong>
        </div>
      </div>
      <ConfigNav :active-section="activeSection" @select="selectSection" />
    </aside>
    <main class="instance-settings-main">
      <header class="instance-settings-toolbar">
        <div>
          <span class="section-eyebrow">{{ t("config.instanceEyebrow") }}</span>
          <h1>{{ t(`config.nav.${activeSection}`) }}</h1>
        </div>
        <button
          ref="closeButton"
          type="button"
          class="settings-close"
          :aria-label="t('config.close')"
          @click="requestClose"
        >
          <span aria-hidden="true">×</span><small>{{ t("config.escapeHint") }}</small>
        </button>
      </header>
      <div class="instance-settings-content">
        <component :is="activePage" @dirty-change="dirty = $event" />
      </div>
    </main>
    <ConfirmDialog
      v-if="pendingAction"
      :title="t('config.unsavedTitle')"
      :message="t('config.unsavedConfirm')"
      :confirm-label="t('config.discardChanges')"
      danger
      @cancel="pendingAction = null"
      @confirm="confirmDiscard"
    />
  </section>
</template>
