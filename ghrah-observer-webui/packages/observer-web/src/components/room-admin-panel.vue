<!--
SPDX-FileCopyrightText: 2026 chenxya <chenxya@ghrah.org>

SPDX-License-Identifier: Apache-2.0
-->

<script setup lang="ts">
import { useAgentsStore, useRoomsStore } from "@ghrah/observer-core";
import { computed, nextTick, ref, watch } from "vue";
import { useI18n } from "vue-i18n";
import ConfirmDialog from "@/components/ui/confirm-dialog.vue";
import { useObserver } from "@/composables/useObserver";
import { useRoomMemberLabel } from "@/composables/useRoomMemberLabel";
import UiButton from "@/components/ui/button.vue";
import UiSelect from "@/components/ui/select.vue";

const emit = defineEmits<{
  roomInvalidated: [target: { projectId: string; roomId: string }];
}>();
const { t } = useI18n();
const rooms = useRoomsStore();
const agents = useAgentsStore();
const memberLabel = useRoomMemberLabel();
const {
  updateRoom,
  archiveRoom,
  deleteRoom,
  joinRoom,
  leaveRoom,
  listRooms,
  error: observerError,
} = useObserver();

const room = computed(() => rooms.activeRoom);
const nameDraft = ref("");
const baselineName = ref("");
const selectedAgentId = ref("");
const busy = ref<"rename" | "member" | "archive" | "delete" | "refresh" | null>(null);
const localError = ref<string | null>(null);
const localErrorCode = ref<string | null>(null);
const pendingLifecycle = ref<"archive" | "delete" | null>(null);
let trackedRoomId: string | undefined;

const nameDirty = computed(
  () =>
    !!room.value && nameDraft.value.trim() !== "" && nameDraft.value.trim() !== baselineName.value,
);
const versionConflict = computed(() => localErrorCode.value === "room_version_conflict");
const candidateAgents = computed(() => {
  const current = room.value;
  if (!current) return [];
  return agents
    .activeAgentsForProject(current.project_id)
    .filter(
      (agent) =>
        !current.members.some(
          (member) =>
            member.subject === agent.agentId ||
            member.subject === agent.agentName ||
            member.subject_name === agent.agentName,
        ),
    );
});

watch(
  () => [room.value?.room_id, room.value?.name] as const,
  ([roomId, roomName]) => {
    if (roomId !== trackedRoomId || !nameDirty.value) {
      nameDraft.value = roomName ?? "";
      baselineName.value = roomName ?? "";
    }
    if (roomId !== trackedRoomId) {
      selectedAgentId.value = "";
      localError.value = null;
      localErrorCode.value = null;
      pendingLifecycle.value = null;
    }
    trackedRoomId = roomId;
  },
  { immediate: true },
);

function clearFailure() {
  localError.value = null;
  localErrorCode.value = null;
}

function setFailure(
  result: { error?: string | null; error_detail?: string | null } | null,
  fallback: string,
) {
  localErrorCode.value = result?.error ?? null;
  localError.value = result?.error_detail ?? result?.error ?? observerError.value ?? fallback;
}

async function renameRoom() {
  const current = room.value;
  const nextName = nameDraft.value.trim();
  if (!current || !nameDirty.value || busy.value) return;
  busy.value = "rename";
  clearFailure();
  try {
    const result = await updateRoom(current.room_id, nextName, current.version);
    if (result?.success) baselineName.value = nextName;
    else setFailure(result, t("roomAdmin.renameFailed"));
  } finally {
    busy.value = null;
  }
}

async function addMember() {
  const current = room.value;
  const agent = candidateAgents.value.find((item) => item.agentId === selectedAgentId.value);
  if (!current || !agent || busy.value) return;
  busy.value = "member";
  clearFailure();
  try {
    const result = await joinRoom(current.room_id, agent.agentId, "agent", agent.agentName);
    if (result?.success) selectedAgentId.value = "";
    else setFailure(result, t("nav.room.joinFailed"));
  } finally {
    busy.value = null;
  }
}

async function removeMember(subject: string) {
  const current = room.value;
  if (!current || busy.value) return;
  busy.value = "member";
  clearFailure();
  try {
    const result = await leaveRoom(current.room_id, subject);
    if (!result?.success) setFailure(result, t("nav.room.removeFailed"));
  } finally {
    busy.value = null;
  }
}

async function runLifecycle() {
  const current = room.value;
  const action = pendingLifecycle.value;
  pendingLifecycle.value = null;
  if (!current || !action || busy.value) return;
  busy.value = action;
  clearFailure();
  try {
    const result =
      action === "archive"
        ? await archiveRoom(current.room_id, current.version)
        : await deleteRoom(current.room_id, current.version);
    if (result?.success) {
      emit("roomInvalidated", { projectId: current.project_id, roomId: current.room_id });
    } else
      setFailure(
        result,
        action === "archive" ? t("roomAdmin.archiveFailed") : t("roomAdmin.deleteFailed"),
      );
  } finally {
    busy.value = null;
  }
}

async function refreshRoom() {
  const current = room.value;
  if (!current || busy.value) return;
  const preservedDraft = nameDraft.value;
  busy.value = "refresh";
  try {
    const result = await listRooms(current.project_id, "active");
    if (result?.success) {
      clearFailure();
      await nextTick();
      baselineName.value = room.value?.name ?? "";
      nameDraft.value = preservedDraft;
    } else setFailure(result, t("roomAdmin.refreshFailed"));
  } finally {
    busy.value = null;
  }
}
</script>

<template>
  <div class="room-admin-panel sidebar-section">
    <div class="section-heading room-admin-heading">
      <div>
        <span class="section-eyebrow">{{ t("roomAdmin.eyebrow") }}</span>
        <h3>{{ room?.name ?? t("roomAdmin.title") }}</h3>
      </div>
      <span v-if="room" class="room-version">v{{ room.version }}</span>
    </div>
    <div v-if="!room" class="room-admin-empty">
      <span aria-hidden="true">#</span>
      <p>{{ t("roomAdmin.selectRoom") }}</p>
    </div>
    <div v-else class="room-admin-content">
      <p v-if="localError" class="room-admin-error" role="alert">
        <span>{{ localError }}</span>
        <button v-if="versionConflict" type="button" :disabled="busy !== null" @click="refreshRoom">
          {{ t("roomAdmin.refresh") }}
        </button>
      </p>
      <section class="room-admin-section">
        <h4>{{ t("roomAdmin.details") }}</h4>
        <form class="room-rename-form" @submit.prevent="renameRoom">
          <label for="active-room-name">{{ t("roomAdmin.name") }}</label>
          <div>
            <input
              id="active-room-name"
              v-model="nameDraft"
              type="text"
              :disabled="busy !== null"
            /><UiButton type="submit" variant="secondary" :disabled="!nameDirty || busy !== null">
              {{ busy === "rename" ? t("common.loading") : t("common.save") }}
            </UiButton>
          </div>
        </form>
      </section>
      <section class="room-admin-section room-members-section">
        <div class="room-admin-section-heading">
          <h4>{{ t("roomAdmin.members") }}</h4>
          <span>{{ room.members.length }}</span>
        </div>
        <ul class="room-admin-members">
          <li v-for="member in room.members" :key="member.subject">
            <span class="room-admin-member-avatar" aria-hidden="true">{{
              member.subject_type === "human" ? "H" : "A"
            }}</span>
            <span class="room-admin-member-name" :title="memberLabel(member, room.project_id)">{{
              memberLabel(member, room.project_id)
            }}</span>
            <button
              type="button"
              :aria-label="
                t('roomAdmin.removeMember', { name: memberLabel(member, room.project_id) })
              "
              :disabled="busy !== null"
              @click="removeMember(member.subject)"
            >
              ×
            </button>
          </li>
          <li v-if="room.members.length === 0" class="room-admin-members-empty">
            {{ t("nav.room.noMembers") }}
          </li>
        </ul>
        <form class="room-member-add" @submit.prevent="addMember">
          <UiSelect
            v-model="selectedAgentId"
            class="min-w-0 flex-1"
            :aria-label="t('roomAdmin.addAgent')"
            :disabled="busy !== null || candidateAgents.length === 0"
          >
            <option value="">
              {{
                candidateAgents.length ? t("roomAdmin.chooseAgent") : t("nav.room.allAgentsInRoom")
              }}
            </option>
            <option v-for="agent in candidateAgents" :key="agent.agentId" :value="agent.agentId">
              {{ agent.agentName }}
            </option>
          </UiSelect>
          <UiButton type="submit" variant="secondary" :disabled="!selectedAgentId || busy !== null">
            {{ t("roomAdmin.add") }}
          </UiButton>
        </form>
      </section>
      <section class="room-admin-section room-lifecycle-section">
        <h4>{{ t("roomAdmin.lifecycle") }}</h4>
        <p>{{ t("roomAdmin.archiveHint") }}</p>
        <UiButton
          type="button"
          variant="secondary"
          :disabled="busy !== null"
          @click="pendingLifecycle = 'archive'"
        >
          {{ t("roomAdmin.archive") }}
        </UiButton>
      </section>
      <section class="room-danger-zone">
        <strong>{{ t("roomAdmin.dangerZone") }}</strong>
        <p>{{ t("roomAdmin.deleteHint") }}</p>
        <button
          type="button"
          class="room-danger-button"
          :disabled="busy !== null"
          @click="pendingLifecycle = 'delete'"
        >
          {{ t("roomAdmin.delete") }}
        </button>
      </section>
    </div>
    <ConfirmDialog
      v-if="pendingLifecycle"
      :title="
        pendingLifecycle === 'archive' ? t('roomAdmin.archiveTitle') : t('roomAdmin.deleteTitle')
      "
      :message="
        pendingLifecycle === 'archive'
          ? t('roomAdmin.archiveConfirm')
          : t('roomAdmin.deleteConfirm')
      "
      :confirm-label="
        pendingLifecycle === 'archive' ? t('roomAdmin.archive') : t('roomAdmin.delete')
      "
      :danger="pendingLifecycle === 'delete'"
      @cancel="pendingLifecycle = null"
      @confirm="runLifecycle"
    />
  </div>
</template>
