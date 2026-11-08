<!--
SPDX-FileCopyrightText: 2026 chenxya <chenxya@ghrah.org>

SPDX-License-Identifier: Apache-2.0
-->

<script setup lang="ts">
import { useProjectsStore } from "@ghrah/observer-core";
import { nextTick, ref } from "vue";
import { useI18n } from "vue-i18n";
import { useObserver } from "@/composables/useObserver";
import UiButton from "@/components/ui/button.vue";

const { t } = useI18n();

const projects = useProjectsStore();
const { switchProject, createProject, error } = useObserver();

const creating = ref(false);
const newName = ref("");
const newDescription = ref("");
const newProjectRoot = ref("");
const newWorkspaces = ref<Array<{ locator: string; name: string }>>([]);
const busy = ref(false);
const nameInput = ref<HTMLInputElement | null>(null);

function startCreate() {
  creating.value = true;
  newName.value = "";
  newDescription.value = "";
  newProjectRoot.value = "";
  newWorkspaces.value = [];
  void nextTick(() => nameInput.value?.focus());
}

function cancelCreate() {
  creating.value = false;
  newName.value = "";
  newDescription.value = "";
  newProjectRoot.value = "";
  newWorkspaces.value = [];
}

function addWorkspace() {
  newWorkspaces.value.push({
    locator: "",
    name: newWorkspaces.value.length ? "workspace" : "default",
  });
}

function removeWorkspace(index: number) {
  newWorkspaces.value.splice(index, 1);
}

async function submitCreate() {
  const name = newName.value.trim();
  if (!name || busy.value) return;
  if (newWorkspaces.value.some((workspace) => !workspace.locator.trim())) return;
  busy.value = true;
  try {
    const result = await createProject(name, {
      description: newDescription.value.trim(),
      projectRootLocator: newProjectRoot.value.trim() || undefined,
      writableWorkspaces: newWorkspaces.value.map((workspace, index) => ({
        locator: workspace.locator.trim(),
        name: workspace.name.trim() || `workspace-${index + 1}`,
        role: index === 0 ? "default" : "workspace",
        defaultForAgents: index === 0,
      })),
    });
    if (result?.success) {
      // PROJECT_CREATED 事件驱动 store 后选定新 project
      const created = (result.data as { project?: { project_id: string } } | undefined)?.project;
      if (created?.project_id) switchProject(created.project_id);
      cancelCreate();
    } else if (result && !result.success) {
      error.value = result.error ?? t("nav.project.createFailed");
    }
  } finally {
    busy.value = false;
  }
}
</script>

<template>
  <div class="project-selector sidebar-section">
    <div class="project-heading">
      <h3>{{ t("nav.project.title") }}</h3>
    </div>

    <ul v-if="projects.projectList.length > 0" class="project-list">
      <li
        v-for="project in projects.projectList"
        :key="project.project_id"
        :title="project.name"
        :aria-label="project.name"
        :aria-current="projects.activeProjectId === project.project_id ? 'true' : undefined"
        role="button"
        tabindex="0"
        :class="[
          'project-item cursor-pointer text-sm transition-colors',
          projects.activeProjectId === project.project_id
            ? 'active text-blue-900 dark:text-blue-100 font-medium'
            : 'hover:bg-gray-100 dark:hover:bg-gray-800',
        ]"
        @click="switchProject(project.project_id)"
        @keydown.enter="switchProject(project.project_id)"
        @keydown.space.prevent="switchProject(project.project_id)"
      >
        <span class="project-avatar">{{ project.name.slice(0, 2).toUpperCase() }}</span>
        <span class="project-name-visually-hidden">{{ project.name }}</span>
        <div
          v-if="projects.activeProjectId === project.project_id"
          class="project-summary"
          role="note"
        >
          <strong>{{ project.name }}</strong>
          <p v-if="project.description">{{ project.description }}</p>
          <span>{{ t("nav.project.internalRoot") }}</span>
          <code :title="project.project_root_locator">
            {{ project.project_root_locator || t("nav.project.legacyRoot") }}
          </code>
          <small v-if="project.project_root_locator">
            {{ t("nav.project.stores") }}
          </small>
          <small>{{
            t("nav.project.summaryWorkspaces", { count: project.workspaces?.length ?? 0 })
          }}</small>
        </div>
      </li>
    </ul>

    <p v-else class="project-empty">{{ t("nav.project.empty") }}</p>

    <div class="project-add-area">
      <span class="project-divider" />
      <button
        v-if="!creating"
        class="project-add"
        type="button"
        :aria-label="t('nav.project.new')"
        :title="t('nav.project.new')"
        @click="startCreate"
      >
        <span aria-hidden="true">+</span>
      </button>

      <form v-else class="project-create-popover" @submit.prevent="submitCreate">
        <div class="project-create-header">
          <strong>{{ t("nav.project.createTitle") }}</strong>
          <button
            type="button"
            class="project-create-cancel"
            :aria-label="t('common.cancel')"
            @click="cancelCreate"
          >
            ×
          </button>
        </div>
        <div class="project-create-fields">
          <label for="new-project-name">{{ t("nav.project.name") }}</label>
          <input
            id="new-project-name"
            ref="nameInput"
            v-model="newName"
            type="text"
            :placeholder="t('nav.project.name')"
            class="project-create-input"
            @keydown.esc="cancelCreate"
          />
          <label for="new-project-description">{{ t("nav.project.description") }}</label>
          <textarea
            id="new-project-description"
            v-model="newDescription"
            rows="3"
            :placeholder="t('nav.project.descriptionPlaceholder')"
            class="project-create-input project-create-textarea"
            @keydown.esc="cancelCreate"
          />
          <label for="new-project-root">{{ t("nav.project.internalRoot") }}</label>
          <input
            id="new-project-root"
            v-model="newProjectRoot"
            type="text"
            :placeholder="t('nav.project.rootPlaceholder')"
            class="project-create-input"
            @keydown.esc="cancelCreate"
          />
          <p class="project-create-hint">
            {{ t("nav.project.rootHint") }}
          </p>
          <div class="project-workspace-heading">
            <label>{{ t("nav.project.writableWorkspaces") }}</label>
            <button type="button" class="project-workspace-add" @click="addWorkspace">
              {{ t("nav.project.addWorkspace") }}
            </button>
          </div>
          <p v-if="newWorkspaces.length === 0" class="project-create-hint">
            {{ t("nav.project.noWorkspacesHint") }}
          </p>
          <div
            v-for="(workspace, index) in newWorkspaces"
            :key="index"
            class="project-workspace-row"
          >
            <input
              v-model="workspace.name"
              type="text"
              :aria-label="t('nav.project.workspaceNameAria', { index: index + 1 })"
              :placeholder="t('nav.project.workspaceName')"
              class="project-create-input project-workspace-name"
            />
            <input
              v-model="workspace.locator"
              type="text"
              :aria-label="t('nav.project.workspacePathAria', { index: index + 1 })"
              :placeholder="t('nav.project.workspacePath')"
              class="project-create-input"
            />
            <button
              type="button"
              class="project-workspace-remove"
              :aria-label="t('nav.project.removeWorkspaceAria', { index: index + 1 })"
              @click="removeWorkspace(index)"
            >
              ×
            </button>
          </div>
          <p v-if="newWorkspaces.length > 0" class="project-create-hint">
            {{ t("nav.project.firstWorkspaceHint") }}
          </p>
        </div>
        <div class="project-create-actions">
          <UiButton
            type="submit"
            variant="primary"
            :disabled="
              busy ||
              !newName.trim() ||
              newWorkspaces.some((workspace) => !workspace.locator.trim())
            "
          >
            {{ busy ? "…" : t("common.create") }}
          </UiButton>
        </div>
      </form>
    </div>
  </div>
</template>
