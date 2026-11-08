<!--
SPDX-FileCopyrightText: 2026 chenxya <chenxya@ghrah.org>

SPDX-License-Identifier: Apache-2.0
-->

<script setup lang="ts">
import type { AbilityBoundToolView, AbilityGrantView, BindToolRequest } from "@ghrah/protocol";
import { grantOperationKeys } from "@ghrah/observer-core";
import { computed } from "vue";
import { useI18n } from "vue-i18n";

const props = defineProps<{
  grants: AbilityGrantView[];
  boundTools: AbilityBoundToolView[];
  expectedRevision: number;
  busy: boolean;
}>();
const emit = defineEmits<{ bind: [request: BindToolRequest]; unbind: [toolName: string] }>();
const { t } = useI18n();

const rows = computed(() =>
  props.grants.flatMap((grant) =>
    grantOperationKeys(grant.members).map((member) => ({
      alias: grant.alias,
      member,
      bound: props.boundTools.find((tool) => tool.alias === grant.alias && tool.member === member),
    })),
  ),
);

function toggle(alias: string, member: string, toolName: string | undefined, event: Event) {
  const on = event.target instanceof HTMLInputElement && event.target.checked;
  if (on) {
    emit("bind", {
      alias,
      member,
      tool_name: member,
      expected_revision: props.expectedRevision,
    });
    return;
  }
  if (toolName) emit("unbind", toolName);
}
</script>

<template>
  <fieldset class="tool-selector">
    <legend>{{ t("assembly.tools.selected") }}</legend>
    <label v-for="row in rows" :key="`${row.alias}:${row.member}`">
      <input
        type="checkbox"
        :checked="Boolean(row.bound)"
        :disabled="busy"
        @change="toggle(row.alias, row.member, row.bound?.name, $event)"
      />
      <code>{{ row.alias }}</code>
      <code>{{ row.member }}</code>
    </label>
  </fieldset>
</template>

<style scoped>
.tool-selector {
  min-width: 0;
  overflow-wrap: anywhere;
  border: 1px solid var(--ui-panel-border);
  font-size: var(--font-ui-note);
}
</style>
