<script setup lang="ts">
import { type AbilityTreeClient } from "@ghrah/observer-core";
import { BindToolRequestSchema, type AbilityTreeView } from "@ghrah/protocol";
import { computed, ref, watch } from "vue";
import { useI18n } from "vue-i18n";

const props = defineProps<{ agentId: string; client: AbilityTreeClient }>();
const { t } = useI18n();
const view = ref<AbilityTreeView | null>(null);
const selected = ref("");
const toolName = ref("");
const loading = ref(false);
const saving = ref(false);
const error = ref("");
let epoch = 0;
const choices = computed(() =>
  (view.value?.abilities ?? []).flatMap((ability) =>
    ability.operations.map((operation) => ({
      value: `${ability.alias}/${operation.key}`,
      alias: ability.alias,
      member: operation.key,
      available: ability.availability === "ready",
    })),
  ),
);
const request = computed(() => {
  const choice = choices.value.find((item) => item.value === selected.value && item.available);
  if (!choice || !view.value) return null;
  const result = BindToolRequestSchema.safeParse({
    alias: choice.alias,
    member: choice.member,
    tool_name: toolName.value,
    expected_revision: view.value.revision,
  });
  return result.success ? result.data : null;
});

async function refresh() {
  const current = ++epoch;
  const target = props.agentId;
  view.value = null;
  selected.value = "";
  loading.value = true;
  saving.value = false;
  error.value = "";
  try {
    const snapshot = await props.client.read(target);
    if (current !== epoch) return;
    if (snapshot.agent_id !== target) throw new Error("Target mismatch");
    view.value = snapshot;
  } catch {
    if (current === epoch) error.value = t("schemeAbilities.failed");
  } finally {
    if (current === epoch) loading.value = false;
  }
}

async function change(tool?: string) {
  if (!view.value || saving.value || (!tool && !request.value)) return;
  const current = epoch;
  const target = props.agentId;
  saving.value = true;
  error.value = "";
  try {
    const result = tool
      ? await props.client.unbindTool(target, {
          tool_name: tool,
          expected_revision: view.value.revision,
        })
      : await props.client.bindTool(target, request.value!);
    if (current !== epoch) return;
    if (result.error) {
      error.value = t(`schemeAbilities.errors.${result.error}`);
    } else if (result.view?.agent_id === target) {
      view.value = result.view;
    } else {
      error.value = t("schemeAbilities.failed");
    }
  } catch {
    if (current === epoch) error.value = t("schemeAbilities.uncertain");
  } finally {
    if (current === epoch) saving.value = false;
  }
}
watch([() => props.agentId, () => props.client], () => void refresh(), { immediate: true });
</script>

<template>
  <section class="scheme-abilities">
    <h3>{{ t("schemeAbilities.title") }}</h3>
    <p>{{ t("schemeAbilities.scope", { agent: agentId }) }}</p>
    <button type="button" class="btn-secondary" :disabled="loading || saving" @click="refresh">
      {{ t("schemeAbilities.refresh") }}
    </button>
    <p v-if="loading" role="status">{{ t("schemeAbilities.loading") }}</p>
    <p v-if="error" role="alert">{{ error }}</p>
    <template v-if="view">
      <p>{{ t("schemeAbilities.revision", { revision: view.revision, run: view.run_id }) }}</p>
      <p v-if="!choices.length">{{ t("schemeAbilities.empty") }}</p>
      <p v-if="choices.some((item) => !item.available)">{{ t("schemeAbilities.unavailable") }}</p>
      <form @submit.prevent="change()">
        <label>
          {{ t("schemeAbilities.operation") }}
          <select v-model="selected" :disabled="saving">
            <option value="">{{ t("schemeAbilities.choose") }}</option>
            <option
              v-for="choice in choices"
              :key="choice.value"
              :value="choice.value"
              :disabled="!choice.available"
            >
              {{ choice.value }}
            </option>
          </select>
        </label>
        <label>
          {{ t("schemeAbilities.toolName") }}
          <input v-model="toolName" :disabled="saving" />
        </label>
        <button type="submit" class="btn-primary" :disabled="saving || !request">
          {{ t("schemeAbilities.bind") }}
        </button>
      </form>
      <h4>{{ t("schemeAbilities.bound") }}</h4>
      <p v-if="!view.bound_tools.length">{{ t("schemeAbilities.noTools") }}</p>
      <ul>
        <li v-for="tool in view.bound_tools" :key="tool.name">
          <code>{{ tool.name }}</code>
          <span>{{ tool.alias }}/{{ tool.member }}</span>
          <button type="button" class="btn-secondary" :disabled="saving" @click="change(tool.name)">
            {{ t("schemeAbilities.unbind") }}
          </button>
        </li>
      </ul>
    </template>
  </section>
</template>

<style scoped>
.scheme-abilities {
  font-size: var(--font-ui-body);
}
</style>
