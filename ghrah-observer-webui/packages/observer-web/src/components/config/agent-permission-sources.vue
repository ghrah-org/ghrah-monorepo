<script setup lang="ts">
import { extractManifestEntry } from "@ghrah/observer-core";
import { load } from "js-yaml";
import { onBeforeUnmount, onMounted, ref, watch } from "vue";
import { useI18n } from "vue-i18n";
import { useObserver } from "@/composables/useObserver";
import PermissionValues from "./permission-values.vue";
const props = defineProps<{ source: string; mode: "legacy" | "builtin"; refreshToken?: object }>();
const { t } = useI18n();
const { getAbility } = useObserver();
const rows = ref<
  Array<{
    name: string;
    override: Record<string, unknown> | null;
    declaration: Record<string, unknown> | null;
    error: string;
  }>
>([]);
const loading = ref(true);
let active = true;
let generation = 0;
async function refresh() {
  const current = ++generation;
  loading.value = true;
  rows.value = [];
  try {
    const draft = load(props.source) as {
      abilities?: Array<{ ref?: string; type?: string; permissions?: Record<string, unknown> }>;
    };
    const result = await Promise.all(
      (draft.abilities ?? []).map(async (entry) => {
        const row = {
          name: entry.ref ?? entry.type ?? "",
          override: entry.permissions ?? null,
          declaration: null as Record<string, unknown> | null,
          error: "",
        };
        if (props.mode === "legacy" && entry.ref) {
          const response = await getAbility(entry.ref);
          const value = response?.success
            ? extractManifestEntry(response.data, { kind: "ability", fullName: entry.ref })
            : null;
          if (value?.kind === "ability") row.declaration = value.summary.permissions;
          else row.error = response?.error ?? t("config.editor.loadFailed");
        }
        return row;
      }),
    );
    if (active && current === generation) rows.value = result;
  } catch (cause) {
    if (active && current === generation)
      rows.value = [{ name: "", override: null, declaration: null, error: String(cause) }];
  } finally {
    if (active && current === generation) loading.value = false;
  }
}
onMounted(() => void refresh());
watch(
  () => props.refreshToken,
  () => void refresh(),
);
onBeforeUnmount(() => {
  active = false;
});
</script>
<template>
  <section class="permission-sources">
    <h4>{{ t("commonConfig.permissionSources") }}</h4>
    <p>{{ t("commonConfig.permissionHint") }}</p>
    <p v-if="loading">{{ t("common.loading") }}</p>
    <article v-for="(row, index) in rows" :key="index">
      <h4>{{ row.name }}</h4>
      <p v-if="row.error" role="alert">{{ row.error }}</p>
      <template v-if="row.declaration"
        ><h5>{{ t("commonConfig.declaration") }}</h5>
        <PermissionValues :permissions="row.declaration"
      /></template>
      <p v-else>{{ t("commonConfig.declarationSource", { mode }) }}</p>
      <h5>{{ t("commonConfig.agentOverride") }}</h5>
      <PermissionValues v-if="row.override" :permissions="row.override" />
      <p v-else>{{ t("commonConfig.noOverride") }}</p>
    </article>
  </section>
</template>
<style scoped>
.permission-sources,
.permission-sources article {
  display: grid;
  gap: var(--spacing-2);
  min-width: 0;
}
.permission-sources h4,
.permission-sources h5,
.permission-sources p {
  margin: 0;
  font-size: var(--font-ui-body);
}
</style>
