<script setup lang="ts">
import { type AgentTarget, useAgentsStore } from "@ghrah/observer-core";
import { onBeforeUnmount, ref, watch } from "vue";
import { useI18n } from "vue-i18n";
import { useObserver } from "@/composables/useObserver";
import AgentAbilitiesDialog from "./agent-abilities-dialog.vue";

const agents = useAgentsStore();
const { t } = useI18n();
const { terminateAgent, agentCompactContext, error: observerError } = useObserver();

const showMenu = ref(false);
const loading = ref(false);
const errorMsg = ref<string | null>(null);
const abilitiesTarget = ref<AgentTarget | null>(null);

function openAbilities() {
  const target = agents.selectedAgentTarget;
  if (!target) return;
  abilitiesTarget.value = { ...target };
  close();
}

function close() {
  showMenu.value = false;
  errorMsg.value = null;
}

function onWindowKeydown(event: KeyboardEvent) {
  if (event.key === "Escape") close();
}

watch(showMenu, (open) => {
  if (open) window.addEventListener("keydown", onWindowKeydown);
  else window.removeEventListener("keydown", onWindowKeydown);
});
onBeforeUnmount(() => window.removeEventListener("keydown", onWindowKeydown));

async function handleCompact() {
  const target = agents.selectedAgentTarget;
  if (!target) return;
  loading.value = true;
  errorMsg.value = null;
  const result = await agentCompactContext(target);
  loading.value = false;
  if (result === null) {
    errorMsg.value = observerError.value ?? t("agents.notConnected");
  } else if (result && !result.success) {
    errorMsg.value = result.error ?? t("agents.compactFailed");
  } else {
    close();
  }
}

async function handleTerminate() {
  const target = agents.selectedAgentTarget;
  if (!target) return;
  loading.value = true;
  errorMsg.value = null;
  const result = await terminateAgent(target);
  loading.value = false;
  if (result === null) {
    errorMsg.value = observerError.value ?? t("agents.notConnected");
  } else if (result && !result.success) {
    errorMsg.value = result.error ?? t("agents.terminateFailed");
  } else {
    close();
  }
}
</script>

<template>
  <div v-if="agents.selectedAgentName" class="relative">
    <button
      type="button"
      class="agent-menu-trigger"
      :title="t('agents.actions')"
      @click="showMenu = !showMenu"
    >
      &#8943;
    </button>

    <div v-if="showMenu" class="agent-menu">
      <div class="agent-menu-header">
        {{ agents.selectedAgentName }}
      </div>

      <div v-if="errorMsg" class="agent-menu-error">
        {{ errorMsg }}
      </div>

      <button type="button" class="agent-menu-item" :disabled="loading" @click="handleCompact">
        {{ t("agents.compactContext") }}
      </button>
      <button type="button" class="agent-menu-item" @click="openAbilities">
        {{ t("agentAbilities.manage") }}
      </button>
      <div class="agent-menu-separator" />
      <button
        type="button"
        class="agent-menu-item agent-menu-item--danger"
        :disabled="loading"
        @click="handleTerminate"
      >
        {{ t("agents.terminateAgent") }}
      </button>
    </div>

    <div v-if="showMenu" class="fixed inset-0 z-30" @click="close" />
    <AgentAbilitiesDialog
      v-if="abilitiesTarget"
      :target="abilitiesTarget"
      @close="abilitiesTarget = null"
    />
  </div>
</template>
