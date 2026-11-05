<script setup lang="ts">
import type { ManifestResolveAgentResponsePayload } from "@ghrah/protocol";
import { useI18n } from "vue-i18n";

defineProps<{ preview: ManifestResolveAgentResponsePayload }>();
const { t } = useI18n();
const json = (value: unknown) => JSON.stringify(value, null, 2);
</script>

<template>
  <div class="manifest-preview">
    <h3>{{ t("config.preview.title") }}</h3>
    <p>{{ t("config.preview.scopeHint") }}</p>
    <dl>
      <dt>{{ t("config.preview.manifest") }}</dt>
      <dd>{{ preview.agent_full_name }}</dd>
      <dt>{{ t("config.preview.input") }}</dt>
      <dd>{{ t(`config.preview.inputs.${preview.input_source}`) }}</dd>
      <dt>{{ t("config.preview.source") }}</dt>
      <dd>{{ preview.ability_source }}</dd>
      <dt>{{ t("config.preview.model") }}</dt>
      <dd>{{ preview.config.endpoint_name }}</dd>
      <dt>{{ t("config.preview.runtimeName") }}</dt>
      <dd>{{ preview.config.name }}</dd>
    </dl>
    <details>
      <summary>{{ t("config.preview.config") }}</summary>
      <pre>{{ json(preview.config) }}</pre>
    </details>
    <h4>{{ t("config.preview.abilities", { count: preview.abilities.length }) }}</h4>
    <article
      v-for="(ability, index) in preview.abilities"
      :key="`${index}:${ability.ability_name}`"
    >
      <h4>{{ ability.ability_name }}</h4>
      <details>
        <summary>{{ t("config.preview.tool") }}</summary>
        <pre v-if="ability.tool_schema !== null">{{ json(ability.tool_schema) }}</pre>
        <p v-else>{{ t("config.preview.noTool") }}</p>
      </details>
      <details open>
        <summary>{{ t("config.preview.permissions") }}</summary>
        <pre>{{ json(ability.permissions) }}</pre>
      </details>
      <details>
        <summary>{{ t("config.preview.implementation") }}</summary>
        <pre>{{ json({ implementation: ability.implementation, hooks: ability.hooks }) }}</pre>
      </details>
    </article>
  </div>
</template>

<style scoped>
.manifest-preview {
  display: grid;
  gap: var(--spacing-3);
  font-size: var(--font-ui-body);
}
.manifest-preview h3,
.manifest-preview h4,
.manifest-preview p {
  margin: 0;
}
.manifest-preview dl {
  display: grid;
  gap: var(--spacing-2);
  margin: 0;
}
.manifest-preview dd {
  margin: 0;
  overflow-wrap: anywhere;
}
.manifest-preview article {
  display: grid;
  gap: var(--spacing-2);
  border: 1px solid currentColor;
  border-radius: var(--radius-md);
  padding: var(--spacing-3);
}
.manifest-preview pre {
  font-size: var(--font-ui-code);
  white-space: pre-wrap;
  overflow-wrap: anywhere;
}
.manifest-preview summary {
  cursor: pointer;
}
</style>
