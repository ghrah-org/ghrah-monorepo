<!--
SPDX-FileCopyrightText: 2026 chenxya <chenxya@ghrah.org>

SPDX-License-Identifier: Apache-2.0
-->

<script setup lang="ts">
import { useAbilityPackagesStore } from "@ghrah/observer-core";
import { computed, onMounted, onUnmounted } from "vue";
import { useI18n } from "vue-i18n";
import UiButton from "@/components/ui/button.vue";

defineEmits<{ dirtyChange: [dirty: boolean] }>();
const packages = useAbilityPackagesStore();
const { t } = useI18n();

const legacy = computed(
  () => packages.status?.legacy_deprecated === true || packages.status?.ability_source === "legacy",
);
const statusLabel = computed(() => {
  if (!packages.status) return t("assembly.statusUnavailable");
  if (legacy.value) return t("assembly.statusLegacy");
  if (packages.status.features.tree) return t("assembly.statusBuiltin");
  return t("assembly.statusUnavailable");
});

onMounted(() => packages.setSettingsVisible(true));
onUnmounted(() => packages.setSettingsVisible(false));
</script>

<template>
  <section class="ability-catalog">
    <header>
      <h2>{{ t("assembly.catalog.title") }}</h2>
      <p>{{ statusLabel }}</p>
      <p v-if="packages.status?.reason">
        {{ t("assembly.reason", { reason: packages.status.reason }) }}
      </p>
      <p v-if="packages.loading">{{ t("assembly.loading") }}</p>
      <p v-if="packages.failed" role="alert">{{ t("assembly.loadFailed") }}</p>
    </header>
    <p v-if="legacy">{{ t("assembly.catalog.legacy") }}</p>
    <p v-else-if="packages.status && !packages.status.features.packages_read">
      {{ t("assembly.catalog.packagesHidden") }}
    </p>
    <p v-if="packages.status && !packages.status.features.tree && !legacy">
      {{ t("assembly.catalog.unavailable") }}
    </p>
    <p v-if="packages.status && !packages.status.features.packages_install">
      {{ t("assembly.catalog.installHidden") }}
    </p>
    <p>{{ t("assembly.catalog.empty") }}</p>
    <UiButton type="button" variant="secondary" @click="packages.load()">
      {{ t("assembly.catalog.refresh") }}
    </UiButton>
  </section>
</template>

<style scoped>
.ability-catalog {
  min-width: 0;
  overflow-wrap: anywhere;
  font-size: var(--font-ui-body);
}
</style>
