<!--
SPDX-FileCopyrightText: 2026 chenxya <chenxya@ghrah.org>

SPDX-License-Identifier: Apache-2.0
-->

<script setup lang="ts">
/**
 * 按模板创建 Agent（原 "spawn"）。
 *
 * 模板只提供配置，Agent 一定属于某个项目；这里选项目 + 起名 + 是否立即启动，
 * 走 `POST /api/v1/projects/{project_id}/agents`，`expected_revision` 用项目当前 version。
 */
import type { HttpClient as HttpClientType } from "@ghrah/observer-core";
import { HttpClient, useConnectionStore } from "@ghrah/observer-core";
import { ProjectInfoPayloadSchema } from "@ghrah/protocol";
import { computed, onMounted, ref } from "vue";
import { useI18n } from "vue-i18n";
import { z } from "zod";
import BaseModal from "@/components/ui/base-modal.vue";
import UiButton from "@/components/ui/button.vue";
import UiTextField from "@/components/ui/text-field.vue";

interface TemplateRef {
  template_id: string;
  revision: number;
  metadata: { namespace: string; name: string };
}

const props = defineProps<{ template: TemplateRef }>();
const emit = defineEmits<{
  close: [];
  created: [agentName: string, projectName: string];
}>();

const { t } = useI18n();
const connection = useConnectionStore();

const ProjectListSchema = z.object({ projects: z.array(ProjectInfoPayloadSchema) });

interface ProjectOption {
  projectId: string;
  name: string;
  version: number;
}

const loading = ref(false);
const loadingProjects = ref(true);
const error = ref<string | null>(null);
const projects = ref<ProjectOption[]>([]);
const projectId = ref("");
const name = ref(`${props.template.metadata.name}-agent`);
const startNow = ref(true);

function client(): HttpClientType {
  return new HttpClient({ baseUrl: connection.httpBaseUrl, token: connection.apiToken });
}

const canSubmit = computed(() => projectId.value !== "" && name.value.trim() !== "");

onMounted(async () => {
  try {
    const page = ProjectListSchema.parse(await client().get("/api/v1/projects"));
    projects.value = page.projects
      .filter((project) => project.archived_at === null)
      .map((project) => ({
        projectId: project.project_id,
        name: project.name,
        version: project.version,
      }));
    const first = projects.value[0];
    if (first) projectId.value = first.projectId;
  } catch (cause) {
    error.value = cause instanceof Error ? cause.message : t("config.template.projectLoadFailed");
  } finally {
    loadingProjects.value = false;
  }
});

async function submit() {
  if (loading.value || !canSubmit.value) return;
  const project = projects.value.find((item) => item.projectId === projectId.value);
  if (!project) return;
  loading.value = true;
  error.value = null;
  try {
    await client().post(`/api/v1/projects/${project.projectId}/agents`, {
      expected_revision: project.version,
      name: name.value.trim(),
      template_id: props.template.template_id,
      revision: props.template.revision,
      desired_state: startNow.value ? "running" : "stopped",
    });
    emit("created", name.value.trim(), project.name);
  } catch (cause) {
    error.value = cause instanceof Error ? cause.message : t("config.template.spawnFailed");
  } finally {
    loading.value = false;
  }
}
</script>

<template>
  <BaseModal :title="t('config.template.spawnTitle')" @request-close="emit('close')">
    <form class="spawn-form" @submit.prevent="submit">
      <p class="spawn-intro">
        {{
          t("config.template.spawnIntro", {
            template: `${template.metadata.namespace}.${template.metadata.name}`,
            revision: template.revision,
          })
        }}
      </p>

      <p v-if="error" class="spawn-alert" role="alert">{{ error }}</p>

      <p v-if="loadingProjects" class="spawn-hint">{{ t("common.loading") }}</p>
      <p v-else-if="projects.length === 0" class="spawn-warn">
        {{ t("config.template.noProject") }}
      </p>

      <label class="spawn-field">
        <span>{{ t("config.template.project") }}</span>
        <select v-model="projectId" data-testid="spawn-project">
          <option v-for="project in projects" :key="project.projectId" :value="project.projectId">
            {{ project.name }}
          </option>
        </select>
      </label>

      <label class="spawn-field">
        <span>{{ t("config.template.agentName") }}</span>
        <UiTextField v-model="name" :placeholder="t('config.template.agentNameExample')" />
        <small>{{ t("config.template.agentNameHint") }}</small>
      </label>

      <label class="spawn-check">
        <input v-model="startNow" type="checkbox" />
        {{ t("config.template.startNow") }}
      </label>
      <small class="spawn-hint">{{ t("config.template.startNowHint") }}</small>

      <footer class="spawn-actions">
        <UiButton type="button" variant="ghost" @click="emit('close')">
          {{ t("common.cancel") }}
        </UiButton>
        <UiButton
          type="submit"
          variant="primary"
          :disabled="loading || !canSubmit || projects.length === 0"
        >
          {{ loading ? t("common.saving") : t("config.template.spawn") }}
        </UiButton>
      </footer>
    </form>
  </BaseModal>
</template>

<style scoped>
.spawn-form {
  display: flex;
  flex-direction: column;
  gap: 0.75rem;
  overflow-wrap: anywhere;
}

.spawn-intro {
  margin: 0;
  color: var(--color-text-muted);
  font-size: var(--font-ui-note);
  line-height: 1.5;
}

.spawn-field {
  display: flex;
  flex-direction: column;
  gap: 0.25rem;
  font-size: var(--font-ui-note);
}

.spawn-field select,
.spawn-field input {
  padding: 0.5rem;
  border: 1px solid var(--color-border);
  border-radius: var(--radius-sm);
  background: var(--color-surface);
  color: var(--color-text);
  font: inherit;
}

.spawn-check {
  display: flex;
  align-items: center;
  gap: 0.5rem;
  font-size: var(--font-ui-note);
}

.spawn-hint {
  margin: 0;
  color: var(--color-text-muted);
  font-size: var(--font-ui-note);
}

.spawn-warn {
  margin: 0;
  color: var(--color-danger);
  font-size: var(--font-ui-note);
}

.spawn-alert {
  margin: 0;
  padding: 0.75rem 1rem;
  border: 1px solid var(--color-danger);
  border-radius: var(--radius-md);
  background: color-mix(in srgb, var(--color-danger) 12%, var(--color-surface));
  color: var(--color-danger);
  font-size: var(--font-ui-note);
}

.spawn-actions {
  display: flex;
  justify-content: flex-end;
  gap: 0.5rem;
}
</style>
