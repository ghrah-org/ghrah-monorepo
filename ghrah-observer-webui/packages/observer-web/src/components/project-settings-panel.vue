<!--
SPDX-FileCopyrightText: 2026 chenxya <chenxya@ghrah.org>

SPDX-License-Identifier: Apache-2.0
-->

<script setup lang="ts">
import { useProjectsStore, useRoomsStore } from "@ghrah/observer-core";
import type { RoomInfoPayload } from "@ghrah/protocol";
import { computed, nextTick, ref, watch } from "vue";
import { useI18n } from "vue-i18n";
import ConfirmDialog from "@/components/ui/confirm-dialog.vue";
import { useObserver } from "@/composables/useObserver";
import { useTabScrollRestore } from "@/composables/useTabScrollRestore";
import UiButton from "@/components/ui/button.vue";

type PendingAction =
  | { kind: "archive-project" }
  | { kind: "delete-project" }
  | { kind: "restore-room"; room: RoomInfoPayload }
  | { kind: "delete-room"; room: RoomInfoPayload };

const props = defineProps<{ projectId: string }>();
const projects = useProjectsStore();
const rooms = useRoomsStore();
const { t } = useI18n();
const {
  updateProject,
  archiveProject,
  deleteProject,
  restoreRoom,
  deleteRoom,
  listProjects,
  listRooms,
  error: observerError,
} = useObserver();

const project = computed(() => projects.projects.get(props.projectId) ?? null);
const activeRooms = computed(() => rooms.roomsForProject(props.projectId, "active"));
const archivedRooms = computed(() =>
  rooms.roomsForProject(props.projectId, "archived").sort((a, b) => a.name.localeCompare(b.name)),
);
const roomCount = computed(() => activeRooms.value.length + archivedRooms.value.length);
const parentArchived = computed(() => !project.value || project.value.archived_at != null);

const nameDraft = ref("");
const descriptionDraft = ref("");
const manifestDraft = ref("");
const baseline = ref({ name: "", description: "", manifest: "" });
const deleteName = ref("");
const cascadeRooms = ref(false);
const serverHasRooms = ref(false);
const busy = ref<string | null>(null);
const errorCode = ref<string | null>(null);
const errorMessage = ref<string | null>(null);
const pendingAction = ref<PendingAction | null>(null);
const panelRoot = ref<HTMLElement | null>(null);
const { onScroll: onPanelScroll } = useTabScrollRestore(panelRoot);

const dirty = computed(
  () =>
    !!project.value &&
    (nameDraft.value.trim() !== baseline.value.name ||
      descriptionDraft.value.trim() !== baseline.value.description ||
      manifestDraft.value.trim() !== baseline.value.manifest),
);
const cascadeRequired = computed(() => roomCount.value > 0 || serverHasRooms.value);
const deleteReady = computed(
  () =>
    !!project.value &&
    deleteName.value === project.value.name &&
    (!cascadeRequired.value || cascadeRooms.value),
);
const versionConflict = computed(
  () =>
    errorCode.value === "project_version_conflict" || errorCode.value === "room_version_conflict",
);

watch(
  () => project.value,
  (next, previous) => {
    const switched = next?.project_id !== previous?.project_id;
    if (switched || !dirty.value) resetDraft(next);
    if (switched) {
      deleteName.value = "";
      cascadeRooms.value = false;
      serverHasRooms.value = false;
      clearFailure();
      pendingAction.value = null;
      void refreshRooms();
    }
  },
  { immediate: true },
);

function resetDraft(current = project.value) {
  const values = {
    name: current?.name ?? "",
    description: current?.description ?? "",
    manifest: current?.manifest_ref ?? "",
  };
  nameDraft.value = values.name;
  descriptionDraft.value = values.description;
  manifestDraft.value = values.manifest;
  baseline.value = values;
}

function clearFailure() {
  errorCode.value = null;
  errorMessage.value = null;
}

function setFailure(
  result: { error?: string | null; error_detail?: string | null } | null,
  fallback: string,
) {
  errorCode.value = result?.error ?? null;
  errorMessage.value = result?.error_detail ?? result?.error ?? observerError.value ?? fallback;
}

async function refreshRooms() {
  if (!project.value) return;
  const projectId = project.value.project_id;
  const [activeResult, archivedResult] = await Promise.all([
    listRooms(projectId, "active"),
    listRooms(projectId, "archived"),
  ]);
  if (!activeResult?.success) setFailure(activeResult, t("projectSettings.roomsLoadFailed"));
  else if (!archivedResult?.success)
    setFailure(archivedResult, t("projectSettings.roomsLoadFailed"));
}

async function saveProject() {
  const current = project.value;
  const name = nameDraft.value.trim();
  if (!current || !name || !dirty.value || busy.value) return;
  busy.value = "save";
  clearFailure();
  try {
    const values = {
      name,
      description: descriptionDraft.value.trim(),
      manifestRef: manifestDraft.value.trim(),
      expectedVersion: current.version,
    };
    const result = await updateProject(current.project_id, values);
    if (result?.success) {
      baseline.value = {
        name: values.name,
        description: values.description,
        manifest: values.manifestRef,
      };
    } else setFailure(result, t("projectSettings.saveFailed"));
  } finally {
    busy.value = null;
  }
}

function actionTitle(action: PendingAction) {
  return t(`projectSettings.actions.${action.kind}.title`);
}

function actionMessage(action: PendingAction) {
  if ("room" in action) {
    return t(`projectSettings.actions.${action.kind}.message`, { name: action.room.name });
  }
  return t(`projectSettings.actions.${action.kind}.message`);
}

function actionLabel(action: PendingAction) {
  return t(`projectSettings.actions.${action.kind}.confirm`);
}

async function runPendingAction() {
  const action = pendingAction.value;
  pendingAction.value = null;
  const current = project.value;
  if (!action || !current || busy.value) return;
  busy.value = action.kind;
  clearFailure();
  try {
    if (action.kind === "archive-project") {
      const result = await archiveProject(current.project_id, current.version);
      if (!result?.success) setFailure(result, t("projectSettings.archiveFailed"));
      return;
    }
    if (action.kind === "delete-project") {
      const result = await deleteProject(current.project_id, current.version, cascadeRooms.value);
      if (!result?.success) {
        if (result?.error === "project_has_rooms") serverHasRooms.value = true;
        setFailure(result, t("projectSettings.deleteFailed"));
      }
      return;
    }
    if (action.kind === "restore-room") {
      if (parentArchived.value) return;
      const result = await restoreRoom(action.room.room_id, action.room.version);
      if (!result?.success) setFailure(result, t("projectSettings.roomRestoreFailed"));
      else await refreshRooms();
      return;
    }
    const result = await deleteRoom(action.room.room_id, action.room.version);
    if (!result?.success) setFailure(result, t("projectSettings.roomDeleteFailed"));
    else await listRooms(current.project_id, "archived");
  } finally {
    busy.value = null;
  }
}

async function refreshAfterConflict() {
  if (busy.value) return;
  const preserved = {
    name: nameDraft.value,
    description: descriptionDraft.value,
    manifest: manifestDraft.value,
  };
  busy.value = "refresh";
  try {
    const result = await listProjects({ archived: project.value?.archived_at != null });
    if (!result?.success) {
      setFailure(result, t("projectSettings.refreshFailed"));
      return;
    }
    await refreshRooms();
    await nextTick();
    baseline.value = {
      name: project.value?.name ?? "",
      description: project.value?.description ?? "",
      manifest: project.value?.manifest_ref ?? "",
    };
    nameDraft.value = preserved.name;
    descriptionDraft.value = preserved.description;
    manifestDraft.value = preserved.manifest;
    clearFailure();
  } finally {
    busy.value = null;
  }
}
</script>

<template>
  <div ref="panelRoot" class="project-panel project-settings-panel" @scroll.passive="onPanelScroll">
    <div v-if="!project" class="project-panel-empty">{{ t("projectSettings.unavailable") }}</div>
    <template v-else>
      <header class="project-panel-header">
        <div>
          <span class="section-eyebrow">{{ t("projectSettings.eyebrow") }}</span>
          <h2>{{ project.name }}</h2>
        </div>
        <span class="project-status-chip">{{ project.status }} · v{{ project.version }}</span>
      </header>

      <div v-if="errorMessage" class="project-panel-error" role="alert">
        <span>{{ errorMessage }}</span>
        <button
          v-if="versionConflict"
          type="button"
          :disabled="busy !== null"
          @click="refreshAfterConflict"
        >
          {{ t("projectSettings.refresh") }}
        </button>
      </div>

      <section class="project-settings-section">
        <h3>{{ t("projectSettings.details") }}</h3>
        <form class="project-settings-form" @submit.prevent="saveProject">
          <label for="project-settings-name">{{ t("projectSettings.name") }}</label>
          <input
            id="project-settings-name"
            v-model="nameDraft"
            type="text"
            :disabled="busy !== null"
          />
          <label for="project-settings-description">{{ t("projectSettings.description") }}</label>
          <textarea
            id="project-settings-description"
            v-model="descriptionDraft"
            rows="3"
            :disabled="busy !== null"
          />
          <label for="project-settings-manifest">{{ t("projectSettings.manifest") }}</label>
          <input
            id="project-settings-manifest"
            v-model="manifestDraft"
            type="text"
            :disabled="busy !== null"
          />
          <UiButton
            type="submit"
            variant="primary"
            :disabled="!dirty || !nameDraft.trim() || busy !== null"
          >
            {{ busy === "save" ? t("common.loading") : t("common.save") }}
          </UiButton>
        </form>
      </section>

      <section class="project-settings-section project-storage-summary">
        <h3>{{ t("projectSettings.storage") }}</h3>
        <dl>
          <div>
            <dt>{{ t("projectSettings.root") }}</dt>
            <dd>
              <code>{{ project.project_root_locator || t("nav.project.legacyRoot") }}</code>
            </dd>
          </div>
          <div>
            <dt>{{ t("projectSettings.workspaces") }}</dt>
            <dd>{{ project.workspaces?.length ?? 0 }}</dd>
          </div>
          <div>
            <dt>{{ t("projectSettings.agents") }}</dt>
            <dd>{{ project.agents?.length ?? 0 }}</dd>
          </div>
          <div>
            <dt>{{ t("projectSettings.rooms") }}</dt>
            <dd>{{ roomCount }}</dd>
          </div>
        </dl>
        <p>{{ t("projectSettings.workspaceBoundary") }}</p>
      </section>

      <section class="project-settings-section archived-room-section">
        <div class="project-settings-section-heading">
          <div>
            <h3>{{ t("projectSettings.archivedRooms") }}</h3>
            <p>{{ t("projectSettings.archivedRoomsHint") }}</p>
          </div>
          <span>{{ archivedRooms.length }}</span>
        </div>
        <p v-if="archivedRooms.length === 0" class="archived-resource-empty">
          {{ t("projectSettings.noArchivedRooms") }}
        </p>
        <ul v-else class="archived-room-list">
          <li v-for="archivedRoom in archivedRooms" :key="archivedRoom.room_id">
            <span class="archived-room-mark" aria-hidden="true">#</span>
            <div>
              <strong>{{ archivedRoom.name }}</strong
              ><small>{{
                t("projectSettings.roomVersion", { version: archivedRoom.version })
              }}</small>
            </div>
            <UiButton
              type="button"
              variant="secondary"
              :disabled="busy !== null || parentArchived"
              :title="parentArchived ? t('projectSettings.restoreProjectFirst') : undefined"
              @click="pendingAction = { kind: 'restore-room', room: archivedRoom }"
            >
              {{ t("projectSettings.restoreRoom") }}
            </UiButton>
            <button
              type="button"
              class="archived-danger-toggle"
              :disabled="busy !== null"
              @click="pendingAction = { kind: 'delete-room', room: archivedRoom }"
            >
              {{ t("projectSettings.deleteRoom") }}
            </button>
          </li>
        </ul>
      </section>

      <section class="project-settings-section project-lifecycle-section">
        <h3>{{ t("projectSettings.lifecycle") }}</h3>
        <p>{{ t("projectSettings.archiveHint") }}</p>
        <UiButton
          type="button"
          variant="secondary"
          :disabled="busy !== null"
          @click="pendingAction = { kind: 'archive-project' }"
        >
          {{ t("projectSettings.archive") }}
        </UiButton>
      </section>

      <section class="project-danger-zone">
        <h3>{{ t("projectSettings.dangerZone") }}</h3>
        <p>{{ t("projectSettings.deleteHint") }}</p>
        <ul>
          <li>{{ t("projectSettings.deleteAgents") }}</li>
          <li>{{ t("projectSettings.deleteHistory") }}</li>
          <li>{{ t("projectSettings.deleteRooms") }}</li>
          <li>{{ t("projectSettings.deleteMemorySkills") }}</li>
        </ul>
        <p>{{ t("projectSettings.externalWorkspacesSafe") }}</p>
        <label for="project-delete-name">{{
          t("projectSettings.confirmName", { name: project.name })
        }}</label>
        <input
          id="project-delete-name"
          v-model="deleteName"
          type="text"
          autocomplete="off"
          :disabled="busy !== null"
        />
        <label v-if="cascadeRequired" class="project-cascade-check">
          <input v-model="cascadeRooms" type="checkbox" :disabled="busy !== null" />
          <span>{{ t("projectSettings.cascadeRooms", { count: roomCount }) }}</span>
        </label>
        <button
          type="button"
          class="project-danger-button"
          :disabled="!deleteReady || busy !== null"
          @click="pendingAction = { kind: 'delete-project' }"
        >
          {{ t("projectSettings.delete") }}
        </button>
      </section>
    </template>

    <ConfirmDialog
      v-if="pendingAction"
      :title="actionTitle(pendingAction)"
      :message="actionMessage(pendingAction)"
      :confirm-label="actionLabel(pendingAction)"
      :danger="pendingAction.kind === 'delete-project' || pendingAction.kind === 'delete-room'"
      @cancel="pendingAction = null"
      @confirm="runPendingAction"
    />
  </div>
</template>
