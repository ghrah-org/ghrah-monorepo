<!--
SPDX-FileCopyrightText: 2026 chenxya <chenxya@ghrah.org>

SPDX-License-Identifier: Apache-2.0
-->

<script setup lang="ts">
import { useProjectsStore, useRoomsStore } from "@ghrah/observer-core";
import type { RoomInfoPayload } from "@ghrah/protocol";
import { computed, onBeforeUnmount, onMounted, ref } from "vue";
import { useI18n } from "vue-i18n";
import ConfirmDialog from "@/components/ui/confirm-dialog.vue";
import SidebarList from "@/components/ui/sidebar-list.vue";
import SidebarRow from "@/components/ui/sidebar-row.vue";
import { useObserver } from "@/composables/useObserver";
import { useRoomMemberLabel } from "@/composables/useRoomMemberLabel";
import UiButton from "@/components/ui/button.vue";
import UiTextField from "@/components/ui/text-field.vue";

const { t } = useI18n();
const rooms = useRoomsStore();
const projects = useProjectsStore();
const memberLabel = useRoomMemberLabel();
const { switchRoom, createRoom, archiveRoom, deleteRoom, error } = useObserver();
const emit = defineEmits<{
  openRoom: [room: { room_id: string; project_id: string; name: string }];
  roomInvalidated: [target: { projectId: string; roomId: string }];
}>();

const visibleRooms = computed<RoomInfoPayload[]>(() => {
  const projectId = projects.activeProjectId;
  return projectId ? rooms.roomsForProject(projectId, "active") : [];
});
const membershipCount = computed(() => {
  const count = new Map<string, number>();
  for (const room of visibleRooms.value) {
    for (const member of room.members) {
      count.set(member.subject, (count.get(member.subject) ?? 0) + 1);
    }
  }
  return count;
});

function isMultiRoom(subject: string): boolean {
  return (membershipCount.value.get(subject) ?? 0) > 1;
}

async function selectRoom(room: RoomInfoPayload) {
  await switchRoom(room.room_id);
  emit("openRoom", { room_id: room.room_id, project_id: room.project_id, name: room.name });
}

const creating = ref(false);
const newName = ref("");
const busy = ref(false);
const actionRoomId = ref<string | null>(null);
const pendingLifecycle = ref<{
  action: "archive" | "delete";
  room: RoomInfoPayload;
} | null>(null);

function closeActionMenuOutside(event: PointerEvent) {
  const target = event.target;
  if (target instanceof Element && target.closest(".sidebar-row-actions")) return;
  actionRoomId.value = null;
}

function closeActionMenuOnEscape(event: KeyboardEvent) {
  if (event.key === "Escape") actionRoomId.value = null;
}

onMounted(() => {
  document.addEventListener("pointerdown", closeActionMenuOutside);
  document.addEventListener("keydown", closeActionMenuOnEscape);
});

onBeforeUnmount(() => {
  document.removeEventListener("pointerdown", closeActionMenuOutside);
  document.removeEventListener("keydown", closeActionMenuOnEscape);
});

function cancelCreate() {
  creating.value = false;
  newName.value = "";
}

async function submitCreate() {
  const name = newName.value.trim();
  const projectId = projects.activeProjectId;
  if (!name || busy.value) return;
  if (!projectId) {
    error.value = t("nav.room.selectProjectFirst");
    return;
  }
  busy.value = true;
  try {
    const result = await createRoom(projectId, name);
    if (result?.success) {
      const created = (result.data as { room?: { room_id: string } } | undefined)?.room;
      if (created?.room_id) {
        await switchRoom(created.room_id);
        emit("openRoom", { room_id: created.room_id, project_id: projectId, name });
      }
      cancelCreate();
    } else if (result && !result.success) {
      error.value = result.error ?? t("nav.room.createFailed");
    }
  } finally {
    busy.value = false;
  }
}

function requestLifecycle(action: "archive" | "delete", room: RoomInfoPayload) {
  actionRoomId.value = null;
  pendingLifecycle.value = { action, room };
}

async function runLifecycle() {
  const pending = pendingLifecycle.value;
  pendingLifecycle.value = null;
  if (!pending || busy.value) return;
  busy.value = true;
  try {
    const result =
      pending.action === "archive"
        ? await archiveRoom(pending.room.room_id, pending.room.version)
        : await deleteRoom(pending.room.room_id, pending.room.version);
    if (result?.success) {
      emit("roomInvalidated", {
        projectId: pending.room.project_id,
        roomId: pending.room.room_id,
      });
    } else if (result && !result.success) {
      error.value =
        result.error ??
        (pending.action === "archive" ? t("roomAdmin.archiveFailed") : t("roomAdmin.deleteFailed"));
    }
  } finally {
    busy.value = false;
  }
}
</script>

<template>
  <div class="room-selector sidebar-section">
    <div class="section-heading">
      <div>
        <span class="section-eyebrow">{{ t("nav.room.workspace") }}</span>
        <h3>{{ t("nav.room.rooms") }}</h3>
      </div>
      <UiButton
        v-if="!creating"
        type="button"
        size="xs"
        variant="primary"
        :disabled="!projects.activeProjectId"
        :title="t('nav.room.new')"
        @click="
          creating = true;
          newName = '';
        "
      >
        + {{ t("common.create") }}
      </UiButton>
    </div>
    <form v-if="creating" class="mb-2 flex gap-1" @submit.prevent="submitCreate">
      <UiTextField
        v-model="newName"
        data-autofocus
        type="text"
        :placeholder="t('nav.room.namePlaceholder')"
        class="flex-1 min-w-0 px-2 py-1 text-sm dark:text-gray-100"
        @keydown.esc="cancelCreate"
      />
      <UiButton type="submit" size="sm" variant="primary" :disabled="busy || !newName.trim()">
        {{ busy ? "…" : t("common.create") }}
      </UiButton>
      <UiButton type="button" size="sm" variant="secondary" @click="cancelCreate">✕</UiButton>
    </form>
    <SidebarList v-if="visibleRooms.length > 0" :label="t('nav.room.rooms')">
      <SidebarRow
        v-for="room in visibleRooms"
        :key="room.room_id"
        class="room-list-row"
        :active="rooms.activeRoomId === room.room_id"
        @select="selectRoom(room)"
      >
        <template #leading><span class="room-list-icon">#</span></template>
        {{ room.name }}
        <template #trailing
          ><span class="room-member-count">{{ room.members.length }}</span></template
        >
        <template v-if="room.members.length > 0" #secondary>
          <span class="room-member-stack">
            <span
              v-for="member in room.members"
              :key="member.subject"
              :title="memberLabel(member, room.project_id)"
              :class="[
                'room-member inline-flex items-center justify-center w-5 h-5 rounded-full text-xs font-semibold',
                isMultiRoom(member.subject)
                  ? 'bg-amber-200 dark:bg-amber-800 text-amber-900 dark:text-amber-100 ring-1 ring-amber-500'
                  : 'bg-gray-200 dark:bg-gray-700 text-gray-600 dark:text-gray-300',
              ]"
              >{{ memberLabel(member, room.project_id).slice(0, 1).toUpperCase() }}</span
            >
          </span>
        </template>
        <template #actions>
          <button
            type="button"
            class="sidebar-row-menu-trigger"
            :aria-label="t('nav.room.actions', { name: room.name })"
            :aria-expanded="actionRoomId === room.room_id"
            @click.stop="actionRoomId = actionRoomId === room.room_id ? null : room.room_id"
          >
            ⋯
          </button>
          <span v-if="actionRoomId === room.room_id" class="sidebar-row-menu">
            <button
              type="button"
              @click.stop="
                selectRoom(room);
                actionRoomId = null;
              "
            >
              {{ t("nav.room.manage") }}
            </button>
            <button type="button" @click.stop="requestLifecycle('archive', room)">
              {{ t("roomAdmin.archive") }}
            </button>
            <button type="button" class="danger" @click.stop="requestLifecycle('delete', room)">
              {{ t("roomAdmin.delete") }}
            </button>
          </span>
        </template>
      </SidebarRow>
    </SidebarList>
    <p v-else class="text-gray-400 dark:text-gray-600 text-sm italic">
      {{ projects.activeProjectId ? t("nav.room.empty") : t("nav.room.selectProjectFirst") }}
    </p>
    <ConfirmDialog
      v-if="pendingLifecycle"
      :title="
        pendingLifecycle.action === 'archive'
          ? t('roomAdmin.archiveTitle')
          : t('roomAdmin.deleteTitle')
      "
      :message="
        pendingLifecycle.action === 'archive'
          ? t('roomAdmin.archiveConfirm')
          : t('roomAdmin.deleteConfirm')
      "
      :confirm-label="
        pendingLifecycle.action === 'archive' ? t('roomAdmin.archive') : t('roomAdmin.delete')
      "
      :danger="pendingLifecycle.action === 'delete'"
      @cancel="pendingLifecycle = null"
      @confirm="runLifecycle"
    />
  </div>
</template>
