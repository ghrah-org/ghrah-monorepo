<!--
SPDX-FileCopyrightText: 2026 chenxya <chenxya@ghrah.org>

SPDX-License-Identifier: Apache-2.0
-->

<script setup lang="ts">
import { useI18n } from "vue-i18n";
import SidebarList from "@/components/ui/sidebar-list.vue";
import SidebarRow from "@/components/ui/sidebar-row.vue";

const { t } = useI18n();
type Section = "general" | "agents" | "abilities" | "plugins" | "tasks" | "archived";
defineProps<{ activeSection: Section }>();
defineEmits<{ select: [section: Section] }>();

const links = [
  { id: "general", labelKey: "config.nav.general" },
  { id: "agents", labelKey: "config.nav.agents" },
  { id: "abilities", labelKey: "config.nav.abilities" },
  { id: "plugins", labelKey: "config.nav.plugins" },
  { id: "tasks", labelKey: "config.nav.tasks" },
  { id: "archived", labelKey: "config.nav.archived" },
] as const;
</script>

<template>
  <nav class="config-section-nav" :aria-label="t('config.nav.label')">
    <SidebarList>
      <SidebarRow
        v-for="link in links"
        :key="link.id"
        class="config-section-link"
        :active="activeSection === link.id"
        @select="$emit('select', link.id)"
      >
        <template #leading
          ><span class="config-section-icon">{{
            link.id.slice(0, 1).toUpperCase()
          }}</span></template
        >
        {{ t(link.labelKey) }}
      </SidebarRow>
    </SidebarList>
  </nav>
</template>
