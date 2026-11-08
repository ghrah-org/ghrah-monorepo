<!--
SPDX-FileCopyrightText: 2026 chenxya <chenxya@ghrah.org>

SPDX-License-Identifier: Apache-2.0
-->

<script setup lang="ts">
import type { AbilityTreeView } from "@ghrah/protocol";
import { useI18n } from "vue-i18n";

defineProps<{ tree: AbilityTreeView | null }>();
const { t } = useI18n();
</script>

<template>
  <div class="ability-tree-view">
    <p v-if="!tree">{{ t("assembly.tree.empty") }}</p>
    <ul v-else>
      <li v-for="ability in tree.abilities" :key="ability.alias">
        <code>{{ ability.alias }}</code>
        <p>{{ t("assembly.tree.availability", { availability: ability.availability }) }}</p>
        <p>{{ t("assembly.versions.current", { digest: ability.version_digest }) }}</p>
        <ul>
          <li v-for="operation in ability.operations" :key="operation.key">
            <code>{{ operation.key }}</code>
            <span>{{
              t("assembly.tree.availability", { availability: operation.availability })
            }}</span>
          </li>
        </ul>
      </li>
    </ul>
  </div>
</template>

<style scoped>
.ability-tree-view {
  min-width: 0;
  overflow-wrap: anywhere;
  font-size: var(--font-ui-note);
}
</style>
