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
    <p v-if="!admin || admin.grants.length === 0">{{ t("assembly.grants.empty") }}</p>
    <ul v-else>
      <li v-for="grant in admin.grants" :key="grant.alias">
        <code>{{ grant.alias }}</code>
        <p>{{ t("assembly.grants.source", { source: grant.source }) }}</p>
        <p>{{ t("assembly.grants.members", { members: grant.members.join(", ") }) }}</p>
        <p>{{ t("assembly.versions.current", { digest: grant.version_digest }) }}</p>
        <ul>
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
  min-width: 0;
  overflow-wrap: anywhere;
  font-size: var(--font-ui-note);
}
</style>
