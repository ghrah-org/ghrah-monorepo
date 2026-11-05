<!--
SPDX-FileCopyrightText: 2026 chenxya <chenxya@ghrah.org>

SPDX-License-Identifier: Apache-2.0
-->

<script setup lang="ts">
import { useProjectsStore, useRoomsStore } from "@ghrah/observer-core";
import type { ProjectInfoPayload } from "@ghrah/protocol";
import { computed, onMounted, ref } from "vue";
import { useI18n } from "vue-i18n";
import ConfirmDialog from "@/components/ui/confirm-dialog.vue";
import { useObserver } from "@/composables/useObserver";

type PendingAction =
  | { kind: "restore"; project: ProjectInfoPayload }
  | { kind: "delete"; project: ProjectInfoPayload };

const projects = useProjectsStore();
const rooms = useRoomsStore();
const { t, locale } = useI18n();
const { listProjects, restoreProject, deleteProject, error: observerError } = useObserver();

const busy = ref<string | null>(null);
const errorCode = ref<string | null>(null);
const errorMessage = ref<string | null>(null);
const pendingAction = ref<PendingAction | null>(null);
const dangerProjectId = ref<string | null>(null);
const deleteName = ref("");
const cascadeRooms = ref(false);
const serverHasRooms = ref(new Set<string>());

const archivedProjects = computed(() =>
  [...projects.archivedProjectList].sort((a, b) => a.name.localeCompare(b.name)),
);
const hasVersionConflict = computed(() => errorCode.value === "project_version_conflict");

function knownRoomCount(projectId: string) {
  return (
    rooms.roomsForProject(projectId, "active").length +
    rooms.roomsForProject(projectId, "archived").length
  );
}

function requiresCascade(projectId: string) {
  return knownRoomCount(projectId) > 0 || serverHasRooms.value.has(projectId);
}

function formatArchivedAt(value: string | null | undefined) {
  if (!value) return t("config.archived.unknownTime");
  const date = new Date(value);
  if (Number.isNaN(date.valueOf())) return value;
  return new Intl.DateTimeFormat(locale.value, {
    dateStyle: "medium",
    timeStyle: "short",
  }).format(date);
}

function setFailure(
  result: { error?: string | null; error_detail?: string | null } | null,
  fallback: string,
) {
  errorCode.value = result?.error ?? null;
  errorMessage.value = result?.error_detail ?? result?.error ?? observerError.value ?? fallback;
}

async function refreshArchivedProjects() {
  if (busy.value) return;
  busy.value = "refresh";
  try {
    const result = await listProjects({ archived: true });
    if (result?.success) {
      errorCode.value = null;
      errorMessage.value = null;
    } else setFailure(result, t("config.archived.loadFailed"));
  } finally {
    busy.value = null;
  }
}

function openDanger(project: ProjectInfoPayload) {
  if (busy.value) return;
  const opening = dangerProjectId.value !== project.project_id;
  dangerProjectId.value = opening ? project.project_id : null;
  if (opening) {
    deleteName.value = "";
    cascadeRooms.value = false;
  }
}

function deleteReady(project: ProjectInfoPayload) {
  return (
    deleteName.value === project.name &&
    (!requiresCascade(project.project_id) || cascadeRooms.value)
  );
}

async function runPendingAction() {
  const action = pendingAction.value;
  pendingAction.value = null;
  if (!action || busy.value) return;
  const current = projects.projects.get(action.project.project_id) ?? action.project;
  busy.value = `${action.kind}:${current.project_id}`;
  errorCode.value = null;
  errorMessage.value = null;
  try {
    if (action.kind === "restore") {
      const result = await restoreProject(current.project_id, current.version);
      if (!result?.success) {
        setFailure(result, t("config.archived.restoreFailed"));
        return;
      }
      await Promise.all([listProjects({ archived: false }), listProjects({ archived: true })]);
      return;
    }

    const result = await deleteProject(current.project_id, current.version, cascadeRooms.value);
    if (!result?.success) {
      if (result?.error === "project_has_rooms") {
        serverHasRooms.value = new Set(serverHasRooms.value).add(current.project_id);
      }
      setFailure(result, t("config.archived.deleteFailed"));
      return;
    }
    dangerProjectId.value = null;
    deleteName.value = "";
    cascadeRooms.value = false;
    await listProjects({ archived: true });
  } finally {
    busy.value = null;
  }
}

onMounted(refreshArchivedProjects);
</script>

<template>
  <div class="archived-resources-page">
    <header class="config-page-header archived-resources-header">
      <div>
        <span class="section-eyebrow">{{ t("config.archived.eyebrow") }}</span>
        <h2>{{ t("config.archived.title") }}</h2>
        <p>{{ t("config.archived.description") }}</p>
      </div>
      <button
        type="button"
        class="btn-secondary"
        :disabled="busy !== null"
        @click="refreshArchivedProjects"
      >
        {{ busy === "refresh" ? t("common.loading") : t("config.archived.refresh") }}
      </button>
    </header>

    <div v-if="errorMessage" class="archived-resources-error" role="alert">
      <span>{{ errorMessage }}</span>
      <button
        v-if="hasVersionConflict"
        type="button"
        :disabled="busy !== null"
        @click="refreshArchivedProjects"
      >
        {{ t("config.archived.refresh") }}
      </button>
    </div>

    <section class="archived-resource-section" aria-labelledby="archived-projects-title">
      <div class="archived-resource-heading">
        <div>
          <h3 id="archived-projects-title">{{ t("config.archived.projects") }}</h3>
          <p>{{ t("config.archived.projectsHint") }}</p>
        </div>
        <span>{{ archivedProjects.length }}</span>
      </div>

      <p v-if="archivedProjects.length === 0" class="archived-resource-empty">
        {{ t("config.archived.noProjects") }}
      </p>
      <ul v-else class="archived-resource-list">
        <li
          v-for="project in archivedProjects"
          :key="project.project_id"
          class="archived-resource-card"
        >
          <div class="archived-resource-summary">
            <span class="archived-resource-icon" aria-hidden="true">{{
              project.name.slice(0, 1).toUpperCase()
            }}</span>
            <div class="archived-resource-copy">
              <strong>{{ project.name }}</strong>
              <code>{{ project.project_id }}</code>
              <span>{{
                t("config.archived.archivedAt", { date: formatArchivedAt(project.archived_at) })
              }}</span>
            </div>
            <dl class="archived-resource-counts">
              <div>
                <dt>{{ t("config.archived.agents") }}</dt>
                <dd>{{ project.agents?.length ?? 0 }}</dd>
              </div>
              <div>
                <dt>{{ t("config.archived.status") }}</dt>
                <dd>{{ project.status }}</dd>
              </div>
            </dl>
            <div class="archived-resource-actions">
              <button
                type="button"
                class="btn-primary"
                :disabled="busy !== null"
                @click="pendingAction = { kind: 'restore', project }"
              >
                {{ t("config.archived.restore") }}
              </button>
              <button
                type="button"
                class="archived-danger-toggle"
                :disabled="busy !== null"
                @click="openDanger(project)"
              >
                {{ t("config.archived.deletePermanently") }}
              </button>
            </div>
          </div>

          <div v-if="dangerProjectId === project.project_id" class="archived-inline-danger">
            <strong>{{ t("config.archived.dangerZone") }}</strong>
            <p>{{ t("config.archived.projectDeleteHint") }}</p>
            <ul>
              <li>{{ t("config.archived.projectDeleteAgents") }}</li>
              <li>{{ t("config.archived.projectDeleteHistory") }}</li>
              <li>{{ t("config.archived.projectDeleteRooms") }}</li>
              <li>{{ t("config.archived.projectDeleteMemorySkills") }}</li>
            </ul>
            <p>{{ t("config.archived.externalWorkspacesSafe") }}</p>
            <label :for="`archived-project-name-${project.project_id}`">
              {{ t("config.archived.typeName", { name: project.name }) }}
            </label>
            <input
              :id="`archived-project-name-${project.project_id}`"
              v-model="deleteName"
              type="text"
              autocomplete="off"
              :disabled="busy !== null"
            />
            <label v-if="requiresCascade(project.project_id)" class="project-cascade-check">
              <input v-model="cascadeRooms" type="checkbox" :disabled="busy !== null" />
              <span>{{
                t("config.archived.cascadeRooms", { count: knownRoomCount(project.project_id) })
              }}</span>
            </label>
            <button
              type="button"
              class="project-danger-button"
              :disabled="!deleteReady(project) || busy !== null"
              @click="pendingAction = { kind: 'delete', project }"
            >
              {{ t("config.archived.deleteProject") }}
            </button>
          </div>
        </li>
      </ul>
    </section>

    <ConfirmDialog
      v-if="pendingAction"
      :title="
        pendingAction.kind === 'restore'
          ? t('config.archived.restoreTitle')
          : t('config.archived.deleteTitle')
      "
      :message="
        pendingAction.kind === 'restore'
          ? t('config.archived.restoreConfirm')
          : t('config.archived.deleteConfirm')
      "
      :confirm-label="
        pendingAction.kind === 'restore'
          ? t('config.archived.restore')
          : t('config.archived.deleteProject')
      "
      :danger="pendingAction.kind === 'delete'"
      @cancel="pendingAction = null"
      @confirm="runPendingAction"
    />
  </div>
</template>
