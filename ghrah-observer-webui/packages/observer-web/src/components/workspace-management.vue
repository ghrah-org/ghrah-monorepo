<script setup lang="ts">
import type { ProjectInfoPayload, WorkspaceGetResponsePayload } from "@ghrah/protocol";
import { WorkspaceGetResponsePayloadSchema } from "@ghrah/protocol";
import { computed, onBeforeUnmount, onMounted, ref, watch } from "vue";
import { useI18n } from "vue-i18n";
import { z } from "zod";
import { useObserver } from "@/composables/useObserver";
defineProps<{ project: ProjectInfoPayload }>();
const { t } = useI18n();
const { connection, listWorkspaces, getWorkspace, registerWorkspace } = useObserver();
const rows = ref<
  Array<{ workspace_id: string; name: string; provider_type: string; locator: string }>
>([]);
const detail = ref<WorkspaceGetResponsePayload | null>(null);
const error = ref("");
const saved = ref(false);
const busy = ref(false);
const locator = ref("");
const name = ref("");
const approved = ref(false);
const uncertain = ref(false);
const connected = computed(() => connection.state === "connected");
let active = true;
let generation = 0;
const listSchema = z.object({
  workspaces: z.array(
    z.object({
      workspace_id: z.string(),
      name: z.string(),
      provider_type: z.string(),
      locator: z.string(),
    }),
  ),
});
async function refresh() {
  if (busy.value || !connected.value) return;
  busy.value = true;
  error.value = "";
  const current = ++generation;
  try {
    const result = await listWorkspaces();
    if (!active || current !== generation) return;
    if (!result?.success) throw new Error(result?.error ?? t("commonConfig.workspaceLoadFailed"));
    rows.value = listSchema.parse(result.data).workspaces;
    uncertain.value = false;
    const id = detail.value?.workspace_id;
    if (id) {
      detail.value = null;
      if (rows.value.some((row) => row.workspace_id === id)) await read(id, current);
    }
  } catch (cause) {
    if (active && current === generation) error.value = String(cause);
  } finally {
    if (active && current === generation) busy.value = false;
  }
}
async function read(id: string, current: number) {
  const result = await getWorkspace(id);
  if (!active || current !== generation) return;
  if (!result?.success) throw new Error(result?.error ?? t("commonConfig.workspaceLoadFailed"));
  const value = WorkspaceGetResponsePayloadSchema.parse(result.data);
  if (value.workspace_id !== id) throw new Error(t("common.invalidResponse"));
  detail.value = value;
}
async function select(id: string) {
  if (busy.value || !connected.value) return;
  const current = ++generation;
  busy.value = true;
  error.value = "";
  detail.value = null;
  try {
    await read(id, current);
  } catch (cause) {
    if (active && current === generation) error.value = String(cause);
  } finally {
    if (active && current === generation) busy.value = false;
  }
}
async function register() {
  if (busy.value || !connected.value || !approved.value || !locator.value.trim() || uncertain.value)
    return;
  const current = ++generation;
  busy.value = true;
  error.value = "";
  saved.value = false;
  uncertain.value = true;
  try {
    const result = await registerWorkspace(locator.value.trim(), name.value.trim());
    if (!active || current !== generation) return;
    if (!result) {
      throw new Error(t("commonConfig.workspaceUncertain"));
    }
    if (!result.success) {
      uncertain.value = false;
      throw new Error(result.error ?? t("commonConfig.workspaceLoadFailed"));
    }
    const id = z.object({ workspace_id: z.string() }).parse(result.data).workspace_id;
    await read(id, current);
    if (!active || current !== generation) return;
    const list = await listWorkspaces();
    if (!active || current !== generation) return;
    if (!list?.success) throw new Error(t("commonConfig.workspaceLoadFailed"));
    rows.value = listSchema.parse(list.data).workspaces;
    saved.value = true;
    uncertain.value = false;
  } catch (cause) {
    if (active && current === generation) error.value = String(cause);
  } finally {
    if (active && current === generation) busy.value = false;
  }
}
onMounted(() => void refresh());
watch(connected, (value) => {
  if (value) void refresh();
  else {
    generation++;
    busy.value = false;
    detail.value = null;
    saved.value = false;
  }
});
onBeforeUnmount(() => {
  active = false;
  generation++;
});
</script>
<template>
  <div class="workspace-management">
    <h3>{{ t("commonConfig.workspaces") }}</h3>
    <p>{{ t("commonConfig.workspaceHint") }}</p>
    <p v-if="!connected" role="alert">{{ t("agents.notConnected") }}</p>
    <p v-if="error" role="alert">{{ error }}</p>
    <p v-if="saved" role="status">{{ t("commonConfig.workspaceSaved") }}</p>
    <p v-if="uncertain">{{ t("commonConfig.workspaceUncertain") }}</p>
    <button class="btn-secondary" :disabled="busy || !connected" @click="refresh">
      {{ t("commonConfig.refreshWorkspaces") }}
    </button>
    <p v-if="busy" role="status">{{ t("common.loading") }}</p>
    <p v-if="!busy && !error && !rows.length">{{ t("commonConfig.noWorkspaces") }}</p>
    <ul>
      <li v-for="row in rows" :key="row.workspace_id">
        <button
          class="btn-secondary"
          :disabled="busy || !connected"
          @click="select(row.workspace_id)"
        >
          {{ row.name }} · {{ row.workspace_id }}
        </button>
        <p>{{ row.locator }}</p>
        <p>
          {{
            t(
              project.workspaces.some((w) => w.workspace_id === row.workspace_id)
                ? "commonConfig.mounted"
                : "commonConfig.notMounted",
            )
          }}
        </p>
      </li>
    </ul>
    <section v-if="detail">
      <h4>{{ t("commonConfig.workspaceDetail") }}</h4>
      <dl>
        <template
          v-for="key in [
            'workspace_id',
            'name',
            'provider_type',
            'subject_id',
            'locator',
            'created_at',
            'updated_at',
            'exists',
            'writable',
            'status_error',
          ] as const"
          :key="key"
          ><dt>{{ t(`commonConfig.workspaceFields.${key}`) }}</dt>
          <dd>
            {{
              key === "status_error" && detail[key] === null
                ? t("commonConfig.no")
                : detail[key] === undefined || detail[key] === null
                  ? t("commonConfig.unknown")
                  : typeof detail[key] === "boolean"
                    ? t(detail[key] ? "commonConfig.yes" : "commonConfig.no")
                    : detail[key]
            }}
          </dd></template
        >
      </dl>
    </section>
    <form @submit.prevent="register">
      <h4>{{ t("commonConfig.registerWorkspace") }}</h4>
      <label>{{ t("commonConfig.workspaceName") }}<input v-model="name" :disabled="busy" /></label
      ><label
        >{{ t("commonConfig.workspaceLocator")
        }}<input v-model="locator" :disabled="busy" required /></label
      ><label class="workspace-check"
        ><input v-model="approved" type="checkbox" :disabled="busy" />{{
          t("commonConfig.workspaceApproval")
        }}</label
      ><button
        class="btn-primary"
        :disabled="busy || !connected || !approved || !locator.trim() || uncertain"
      >
        {{ t("commonConfig.registerWorkspace") }}
      </button>
    </form>
  </div>
</template>
<style scoped>
.workspace-management,
.workspace-management section,
.workspace-management form,
.workspace-management label {
  display: grid;
  gap: var(--spacing-2);
  min-width: 0;
  font-size: var(--font-ui-body);
}
.workspace-management p,
.workspace-management h3,
.workspace-management h4,
.workspace-management dl,
.workspace-management dd {
  margin: 0;
  overflow-wrap: anywhere;
}
.workspace-management dl {
  display: grid;
  grid-template-columns: minmax(0, 1fr) minmax(0, 2fr);
  gap: var(--spacing-2);
}
.workspace-management input {
  font: inherit;
  border: 1px solid var(--ac-node-border);
  padding: var(--spacing-2);
  border-radius: var(--radius-md);
  background: transparent;
  color: inherit;
  min-width: 0;
}
.workspace-management .workspace-check {
  display: flex;
  align-items: center;
  gap: var(--spacing-2);
}
.workspace-management ul {
  padding-inline-start: var(--spacing-4);
}
</style>
