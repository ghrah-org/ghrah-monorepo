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
    <p v-if="!tree" class="ability-state">{{ t("assembly.tree.empty") }}</p>
    <ul v-else class="ability-cards">
      <li v-for="ability in tree.abilities" :key="ability.alias" class="ability-card">
        <code>{{ ability.alias }}</code>
        <p>{{ t("assembly.tree.availability", { availability: ability.availability }) }}</p>
        <p>{{ t("assembly.versions.current", { digest: ability.version_digest }) }}</p>
        <ul class="ability-card-facts">
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
  display: flex;
  min-width: 0;
  flex-direction: column;
  gap: 0.75rem;
  overflow-wrap: anywhere;
  color: var(--color-text);
  font-size: var(--font-ui-note);
}

.ability-cards,
.ability-card-facts {
  display: flex;
  min-width: 0;
  flex-direction: column;
  gap: 0.5rem;
  margin: 0;
  padding: 0;
  list-style: none;
}

.ability-card {
  min-width: 0;
  padding: 0.75rem 1rem;
  border: 1px solid var(--color-border);
  border-radius: var(--radius-md);
  background: var(--color-surface);
}

.ability-card p,
.ability-state {
  margin: 0;
  color: var(--color-text-muted);
  line-height: 1.5;
}

.ability-card-facts {
  margin-top: 0.5rem;
}

.ability-card-facts li {
  display: flex;
  min-width: 0;
  flex-wrap: wrap;
  gap: 0.25rem 0.75rem;
  color: var(--color-text-muted);
}

.ability-card code {
  font-size: var(--font-ui-code);
}

.ability-state {
  padding: 0.75rem 1rem;
  border: 1px solid var(--color-border);
  border-radius: var(--radius-md);
  background: var(--color-surface);
  text-align: center;
}
</style>
