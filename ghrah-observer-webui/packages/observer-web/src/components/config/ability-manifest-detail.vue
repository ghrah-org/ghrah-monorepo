<script setup lang="ts">
import type { ManifestDetail } from "@ghrah/observer-core";
import { computed } from "vue";
import { useI18n } from "vue-i18n";

const props = defineProps<{ detail: ManifestDetail; disabled?: boolean }>();
defineEmits<{ edit: []; view: []; delete: [] }>();
const { t } = useI18n();
const sections = computed(() => {
  const manifest = props.detail.manifest;
  const metadata = manifest.metadata as Record<string, unknown>;
  return [
    { key: "metadata", value: metadata },
    { key: "tool", value: manifest.tool ?? null },
    { key: "implementation", value: manifest.implementation },
    { key: "hooks", value: manifest.hooks ?? {} },
    { key: "permissions", value: metadata.permissions },
  ];
});
const json = (value: unknown) => JSON.stringify(value, null, 2);
</script>

<template>
  <article class="ability-manifest-detail" :aria-label="t('config.ability.detailTitle')">
    <header>
      <h3>{{ detail.full_name }}</h3>
      <div class="ability-detail-actions">
        <button class="btn-secondary" :disabled="disabled" @click="$emit('view')">
          {{ t("config.ability.viewYaml") }}
        </button>
        <button class="btn-secondary" :disabled="disabled" @click="$emit('edit')">
          {{ t("config.ability.editYaml") }}
        </button>
        <button class="btn-danger" :disabled="disabled" @click="$emit('delete')">
          {{ t("config.ability.delete") }}
        </button>
      </div>
    </header>
    <p>{{ t("config.ability.version", { version: String(detail.manifest.version ?? "—") }) }}</p>
    <p>{{ t("config.ability.permissionsHint") }}</p>
    <section v-for="section in sections" :key="section.key" :data-section="section.key">
      <h4>{{ t(`config.ability.${section.key}`) }}</h4>
      <p v-if="section.key === 'tool' && section.value === null">
        {{ t("config.ability.noTool") }}
      </p>
      <pre>{{ json(section.value) }}</pre>
    </section>
    <details>
      <summary>{{ t("config.ability.document") }}</summary>
      <pre>{{ json(detail.manifest) }}</pre>
    </details>
  </article>
</template>

<style scoped>
.ability-manifest-detail {
  display: grid;
  gap: var(--spacing-3);
  min-width: 0;
  padding: var(--spacing-4);
  border: 1px solid var(--ac-node-border);
  border-radius: var(--radius-md);
  background: var(--ac-node-bg);
  color: var(--ac-node-text);
  font-size: var(--font-ui-body);
}
.ability-manifest-detail header,
.ability-detail-actions {
  display: flex;
  align-items: center;
  justify-content: space-between;
  flex-wrap: wrap;
  gap: var(--spacing-2);
}
.ability-manifest-detail h3,
.ability-manifest-detail h4,
.ability-manifest-detail p,
.ability-manifest-detail pre {
  margin: 0;
  font-size: inherit;
  overflow-wrap: anywhere;
}
.ability-manifest-detail section {
  display: grid;
  gap: var(--spacing-2);
  min-width: 0;
}
.ability-manifest-detail pre {
  white-space: pre-wrap;
  font-size: var(--font-ui-code);
}
.ability-manifest-detail summary {
  cursor: pointer;
}
</style>
