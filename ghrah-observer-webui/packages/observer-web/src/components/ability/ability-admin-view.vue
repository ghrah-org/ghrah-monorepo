<!--
SPDX-FileCopyrightText: 2026 chenxya <chenxya@ghrah.org>

SPDX-License-Identifier: Apache-2.0
-->

<script setup lang="ts">
import type { AbilityAdminView } from "@ghrah/protocol";
import { useI18n } from "vue-i18n";

defineProps<{ admin: AbilityAdminView | null }>();
const { t } = useI18n();
</script>

<template>
  <div class="ability-admin-view">
    <p v-if="!admin || admin.grants.length === 0" class="ability-state ability-state-empty">
      {{ t("assembly.grants.empty") }}
    </p>
    <ul v-else class="ability-cards">
      <li v-for="grant in admin.grants" :key="grant.alias" class="ability-card">
        <code>{{ grant.alias }}</code>
        <p>{{ t("assembly.grants.source", { source: grant.source }) }}</p>
        <p>{{ t("assembly.grants.members", { members: grant.members.join(", ") }) }}</p>
        <p>{{ t("assembly.versions.current", { digest: grant.version_digest }) }}</p>
        <ul class="ability-card-facts">
          <li v-for="axis in grant.axes" :key="axis.member">
            <code>{{ axis.member }}</code>
            <span>{{
              axis.visible ? t("assembly.grants.visible") : t("assembly.grants.hidden")
            }}</span>
            <span>{{
              axis.available ? t("assembly.grants.available") : t("assembly.grants.unavailable")
            }}</span>
          </li>
        </ul>
      </li>
    </ul>
  </div>
</template>

<style scoped>
.ability-admin-view {
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
.ability-card-facts,
.ability-state {
  margin: 0;
  color: var(--color-text-muted);
  line-height: 1.5;
}

.ability-card code {
  font-size: var(--font-ui-code);
}

.ability-card-facts {
  margin-top: 0.5rem;
  font-size: var(--font-ui-note);
}

.ability-card-facts li {
  display: flex;
  min-width: 0;
  flex-wrap: wrap;
  gap: 0.25rem 0.75rem;
}

.ability-state {
  padding: 0.75rem 1rem;
  border: 1px solid var(--color-border);
  border-radius: var(--radius-md);
  background: var(--color-surface);
  text-align: center;
}
</style>
