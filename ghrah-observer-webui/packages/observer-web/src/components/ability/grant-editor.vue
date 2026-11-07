<!--
SPDX-FileCopyrightText: 2026 chenxya <chenxya@ghrah.org>

SPDX-License-Identifier: Apache-2.0
-->

<script setup lang="ts">
import type { AbilityGrantView, GrantAbilityRequest } from "@ghrah/protocol";
import { grantOperationKeys, replacementGrant } from "@ghrah/observer-core";
import { computed, ref, watch } from "vue";
import { useI18n } from "vue-i18n";

const props = defineProps<{
  grant: AbilityGrantView;
  expectedRevision: number;
  busy: boolean;
}>();
const emit = defineEmits<{ save: [request: GrantAbilityRequest]; revoke: [] }>();
const { t } = useI18n();

const operations = computed(() => grantOperationKeys(props.grant.members));
const selected = ref<string[]>([]);
const axes = ref<Record<string, { visible: boolean; available: boolean }>>({});

function sync() {
  selected.value = [...operations.value];
  const next: Record<string, { visible: boolean; available: boolean }> = {};
  for (const key of selected.value) {
    const current = props.grant.axes.find(
      (axis) => axis.member === `operations/${key}` || axis.member === key,
    );
    const visible = current?.visible !== false;
    next[key] = { visible, available: visible && current?.available !== false };
  }
  axes.value = next;
}

watch(
  () => [props.grant.alias, props.grant.revision, props.grant.members.join("|")] as const,
  sync,
  {
    immediate: true,
  },
);

const canSave = computed(() => selected.value.length > 0 || props.grant.content.length > 0);

function checked(event: Event): boolean {
  return event.target instanceof HTMLInputElement && event.target.checked;
}

function toggleMember(key: string, event: Event) {
  const on = checked(event);
  selected.value = on ? [...selected.value, key] : selected.value.filter((item) => item !== key);
  if (!on) return;
  axes.value = { ...axes.value, [key]: axes.value[key] ?? { visible: true, available: true } };
}

function setVisible(key: string, event: Event) {
  const visible = checked(event);
  const current = axes.value[key] ?? { visible: true, available: true };
  axes.value = {
    ...axes.value,
    [key]: { visible, available: visible ? current.available : false },
  };
}

function setAvailable(key: string, event: Event) {
  const current = axes.value[key] ?? { visible: true, available: true };
  axes.value = { ...axes.value, [key]: { ...current, available: checked(event) } };
}

function save() {
  if (!canSave.value || props.busy) return;
  emit(
    "save",
    replacementGrant(props.grant, props.expectedRevision, {
      members: selected.value,
      axes: selected.value.map((key) => {
        const axis = axes.value[key] ?? { visible: true, available: true };
        return {
          member: `operations/${key}`,
          visible: axis.visible,
          available: axis.visible && axis.available,
        };
      }),
    }),
  );
}
</script>

<template>
  <form class="grant-editor" @submit.prevent="save">
    <p>
      <code>{{ grant.alias }}</code>
      {{ t("assembly.grants.source", { source: grant.source }) }}
    </p>
    <fieldset>
      <legend>{{ t("assembly.grants.operations") }}</legend>
      <label v-for="key in operations" :key="key">
        <input
          type="checkbox"
          :checked="selected.includes(key)"
          :disabled="busy"
          @change="toggleMember(key, $event)"
        />
        <code>{{ key }}</code>
      </label>
    </fieldset>
    <fieldset>
      <legend>{{ t("assembly.grants.axes") }}</legend>
      <div v-for="key in selected" :key="key">
        <code>{{ key }}</code>
        <label>
          <input
            type="checkbox"
            :checked="axes[key]?.visible !== false"
            :disabled="busy"
            @change="setVisible(key, $event)"
          />
          {{ t("assembly.grants.visible") }}
        </label>
        <label>
          <input
            type="checkbox"
            :checked="axes[key]?.available !== false && axes[key]?.visible !== false"
            :disabled="busy || axes[key]?.visible === false"
            @change="setAvailable(key, $event)"
          />
          {{ t("assembly.grants.available") }}
        </label>
        <p v-if="axes[key]?.visible !== false && axes[key]?.available === false">
          {{ t("assembly.surface.visibleNotExecutable") }}
        </p>
      </div>
    </fieldset>
    <button type="submit" class="btn-secondary" :disabled="busy || !canSave">
      {{ t("assembly.grants.save") }}
    </button>
    <button type="button" class="btn-secondary" :disabled="busy" @click="emit('revoke')">
      {{ t("assembly.grants.revoke") }}
    </button>
  </form>
</template>

<style scoped>
.grant-editor {
  min-width: 0;
  overflow-wrap: anywhere;
  font-size: var(--font-ui-note);
}

.grant-editor fieldset {
  min-width: 0;
  border: 1px solid var(--ui-panel-border);
}
</style>
