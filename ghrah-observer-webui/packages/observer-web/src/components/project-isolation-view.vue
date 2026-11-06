<script setup lang="ts">
import type { ProjectInfoPayload } from "@ghrah/protocol";
import { computed } from "vue";
import { useI18n } from "vue-i18n";
const props = defineProps<{ project: ProjectInfoPayload }>();
const { t } = useI18n();
const fields = [
  "agent_path_grants",
  "agent_private_dir",
  "effect_allowlist",
  "hitl_override",
  "task_scope",
] as const;
const values = computed(() => props.project.isolation);
function display(key: (typeof fields)[number]) {
  const value = values.value?.[key];
  if (value === undefined) return t("commonConfig.notProvided");
  if (value === null)
    return t(
      key === "effect_allowlist" ? "commonConfig.inheritEffects" : "commonConfig.noOverride",
    );
  if (Array.isArray(value) && value.length === 0) return t("commonConfig.emptyEffects");
  if (typeof value === "boolean")
    return t(value ? "commonConfig.enabled" : "commonConfig.disabled");
  return JSON.stringify(value, null, 2);
}
</script>
<template>
  <section class="isolation-view">
    <h3>{{ t("commonConfig.isolation") }}</h3>
    <p>{{ t("commonConfig.isolationHint") }}</p>
    <dl>
      <template v-for="key in fields" :key="key"
        ><dt>{{ t(`commonConfig.isolationFields.${key}`) }}</dt>
        <dd :data-field="key">
          <pre>{{ display(key) }}</pre>
        </dd></template
      >
    </dl>
    <h4>{{ t("commonConfig.entityGrants") }}</h4>
    <p>{{ t("commonConfig.grantsHint") }}</p>
    <dl v-for="agent in project.agents" :key="agent.agent_id">
      <dt>{{ agent.name }} · {{ agent.agent_id }}</dt>
      <dd>
        <pre>{{ JSON.stringify(agent.path_grants ?? [], null, 2) }}</pre>
      </dd>
    </dl>
  </section>
</template>
<style scoped>
.isolation-view {
  display: grid;
  gap: var(--spacing-3);
  font-size: var(--font-ui-body);
  min-width: 0;
}
.isolation-view p,
.isolation-view h3,
.isolation-view h4,
.isolation-view dl,
.isolation-view dd,
.isolation-view pre {
  margin: 0;
}
.isolation-view dl {
  display: grid;
  gap: var(--spacing-2);
}
.isolation-view dt {
  font-weight: 600;
}
.isolation-view pre {
  white-space: pre-wrap;
  overflow-wrap: anywhere;
  font-size: var(--font-ui-code);
}
</style>
