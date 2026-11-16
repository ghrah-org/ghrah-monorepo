<!--
SPDX-FileCopyrightText: 2026 chenxya <chenxya@ghrah.org>
SPDX-License-Identifier: Apache-2.0
-->
<script setup lang="ts">
import { onMounted, ref } from "vue";
import { useI18n } from "vue-i18n";
import { useObserver } from "@/composables/useObserver";
import UiButton from "@/components/ui/button.vue";
const props = defineProps<{ projectId: string }>();
const emit = defineEmits<{ changed: [] }>();
const { t } = useI18n();
const { listShellAllow, deleteShellAllow } = useObserver();
type AllowList = Awaited<ReturnType<typeof listShellAllow>>;
const data = ref<AllowList | null>(null);
const busy = ref(false);
const error = ref("");
async function refresh() {
  busy.value = true;
  error.value = "";
  try {
    data.value = await listShellAllow(props.projectId);
  } catch (e) {
    error.value = t("shellAllow.loadFailed", { error: e instanceof Error ? e.message : String(e) });
  } finally {
    busy.value = false;
  }
}
async function remove(entry: AllowList["entries"][number]) {
  busy.value = true;
  error.value = "";
  try {
    await deleteShellAllow(props.projectId, entry.kind, entry.key);
    data.value!.entries = data.value!.entries.filter(
      (item) => item.kind !== entry.kind || item.key !== entry.key,
    );
    emit("changed");
  } catch (e) {
    error.value = t("shellAllow.deleteFailed", {
      error: e instanceof Error ? e.message : String(e),
    });
  } finally {
    busy.value = false;
  }
}
onMounted(refresh);
</script>
<template>
  <section class="project-settings-section flex flex-col gap-3">
    <div class="flex flex-wrap items-center justify-between gap-2">
      <h3>{{ t("shellAllow.title") }}</h3>
      <UiButton variant="secondary" :disabled="busy" @click="refresh">{{
        t("projectSettings.refresh")
      }}</UiButton>
    </div>
    <p>{{ t("shellAllow.hint") }}</p>
    <p v-if="busy" role="status">{{ t("common.loading") }}</p>
    <p v-if="error" role="alert">{{ error }}</p>
    <template v-if="data">
      <h4>{{ t("shellAllow.remembered") }}</h4>
      <p v-if="data.entries.length === 0">{{ t("shellAllow.empty") }}</p>
      <ul v-else class="flex flex-col gap-2">
        <li
          v-for="entry in data.entries"
          :key="JSON.stringify([entry.kind, entry.key])"
          class="flex flex-wrap items-center gap-2"
        >
          <span class="text-sm">{{ t(`shellAllow.${entry.kind}`) }}</span
          ><span class="flex-1 min-w-0 break-all whitespace-pre-wrap">{{ entry.label }}</span>
          <UiButton
            variant="danger"
            :disabled="busy"
            :aria-label="t('shellAllow.deleteEntry', { label: entry.label })"
            @click="remove(entry)"
            >{{ t("common.delete") }}</UiButton
          >
        </li>
      </ul>
      <h4>{{ t("shellAllow.builtins") }}</h4>
      <ul class="flex flex-col gap-2">
        <li v-for="rule in data.builtins" :key="rule.key" class="flex flex-wrap gap-2">
          <span class="min-w-0 break-all">{{ rule.label }}</span
          ><span class="text-sm">{{ t("shellAllow.readonly") }}</span>
        </li>
      </ul>
    </template>
  </section>
</template>
