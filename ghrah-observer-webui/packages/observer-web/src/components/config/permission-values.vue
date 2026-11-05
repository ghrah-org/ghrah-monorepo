<script setup lang="ts">
import { useI18n } from "vue-i18n";
defineProps<{ permissions: Record<string, unknown> | null | undefined }>();
const { t } = useI18n();
const fields = [
  "require_hitl",
  "fs_read_only",
  "fs_write",
  "net_access",
  "shell_access",
  "allowed_paths",
  "denied_paths",
  "allowed_commands",
  "denied_commands",
];
function display(value: unknown) {
  return value === undefined
    ? t("commonConfig.notDeclared")
    : typeof value === "boolean"
      ? t(value ? "commonConfig.enabled" : "commonConfig.disabled")
      : JSON.stringify(value);
}
</script>
<template>
  <dl class="permission-values">
    <template v-for="field in fields" :key="field"
      ><dt>{{ t(`config.permission.${field}`) }}</dt>
      <dd>{{ display(permissions?.[field]) }}</dd></template
    >
  </dl>
</template>
<style scoped>
.permission-values {
  display: grid;
  grid-template-columns: minmax(0, 1fr) minmax(0, 1fr);
  gap: var(--spacing-2);
  font-size: var(--font-ui-body);
  margin: 0;
}
.permission-values dd {
  margin: 0;
  white-space: pre-wrap;
  overflow-wrap: anywhere;
  font-family: ui-monospace, monospace;
}
</style>
