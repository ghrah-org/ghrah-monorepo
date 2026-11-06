<script setup lang="ts">
import { useConnectionStore, useRoomsStore } from "@ghrah/observer-core";
import { computed, nextTick, ref, watch } from "vue";
import { useI18n } from "vue-i18n";
import RoomAdminPanel from "@/components/room-admin-panel.vue";
import { useRoomMemberLabel } from "@/composables/useRoomMemberLabel";

/** 投递诊断项：展示名已由父组件解析，避免抽屉重复解析成员。 */
export type DeliveryDiagnostic = {
  id: string;
  recipient: string;
  state: string;
  detail?: string;
};

const props = defineProps<{
  open: boolean;
  roomId: string;
  diagnostics: DeliveryDiagnostic[];
  refreshing: boolean;
  deliveryError: string;
}>();

const emit = defineEmits<{
  close: [];
  refresh: [];
  roomInvalidated: [target: { projectId: string; roomId: string }];
}>();

const { t } = useI18n();
const rooms = useRoomsStore();
const connection = useConnectionStore();
const memberLabel = useRoomMemberLabel();

const panel = ref<HTMLElement | null>(null);
const room = computed(() => rooms.rooms.get(props.roomId) ?? null);
const canRefresh = computed(() => connection.state === "connected");

const stateLabels = new Set([
  "pending",
  "processing",
  "completed",
  "failed",
  "interrupted",
  "blocked",
]);

function stateLabel(state: string): string {
  return t(`agentEntity.deliveryStates.${stateLabels.has(state) ? state : "unknown"}`);
}

watch(
  () => props.open,
  (open) => {
    if (open) void nextTick(() => panel.value?.focus());
  },
);
</script>

<template>
  <div v-if="open" class="room-detail">
    <button
      type="button"
      class="room-detail-backdrop"
      :aria-label="t('chat.detail.close')"
      @click="emit('close')"
    />
    <aside
      id="room-detail-drawer"
      ref="panel"
      class="room-detail-panel"
      role="dialog"
      aria-modal="true"
      :aria-label="t('chat.detail.title')"
      tabindex="-1"
      @keydown.esc="emit('close')"
    >
      <header class="room-detail-head">
        <div>
          <span class="room-detail-eyebrow">{{ t("chat.detail.eyebrow") }}</span>
          <h2>{{ room?.name ?? t("chat.detail.title") }}</h2>
        </div>
        <button type="button" class="btn-secondary" @click="emit('close')">
          {{ t("chat.detail.close") }}
        </button>
      </header>

      <div class="room-detail-body">
        <section class="room-detail-section">
          <h3>{{ t("chat.detail.summary") }}</h3>
          <p class="room-detail-note">
            {{ t("chat.detail.connection", { state: t(`app.status.${connection.state}`) }) }}
          </p>
          <p class="room-detail-note">
            {{ t("chat.detail.members", { count: room?.members.length ?? 0 }) }}
          </p>
        </section>

        <section class="room-detail-section">
          <h3>{{ t("chat.detail.diagnostics") }}</h3>
          <p class="room-detail-note">
            {{ t("chat.detail.deliveries", { count: diagnostics.length }) }}
          </p>
          <ul v-if="diagnostics.length > 0" class="room-detail-diagnostics">
            <li v-for="item in diagnostics" :key="item.id">
              <span class="room-detail-diagnostic-name">{{ item.recipient }}</span>
              <span class="room-detail-diagnostic-state">{{ stateLabel(item.state) }}</span>
              <span v-if="item.detail" class="room-detail-diagnostic-error" role="alert">
                {{ item.detail }}
              </span>
            </li>
          </ul>
          <p v-else class="room-detail-note">{{ t("chat.detail.deliveriesEmpty") }}</p>
          <button
            type="button"
            class="btn-secondary"
            :disabled="refreshing || !canRefresh"
            @click="emit('refresh')"
          >
            {{ t("agentEntity.deliveryRefresh") }}
          </button>
          <p class="room-detail-hint">{{ t("agentEntity.deliveryHint") }}</p>
          <p v-if="deliveryError" class="room-detail-error" role="alert">{{ deliveryError }}</p>
        </section>

        <section class="room-detail-section">
          <h3>{{ t("chat.detail.membersTitle") }}</h3>
          <ul v-if="room && room.members.length > 0" class="room-detail-members">
            <li v-for="member in room.members" :key="member.subject">
              {{ memberLabel(member, room.project_id) }}
            </li>
          </ul>
          <p v-else class="room-detail-note">{{ t("chat.detail.membersEmpty") }}</p>
        </section>

        <!-- Room 管理（重命名 / 成员 / 生命周期 / 危险操作）从右栏迁入，按需展开 -->
        <RoomAdminPanel @room-invalidated="emit('roomInvalidated', $event)" />
      </div>
    </aside>
  </div>
</template>

<style scoped>
.room-detail {
  position: absolute;
  inset: 0;
  z-index: 30;
  display: flex;
  justify-content: flex-end;
}

.room-detail-backdrop {
  position: absolute;
  inset: 0;
  border: 0;
  padding: 0;
  background: var(--ui-overlay);
  cursor: pointer;
}

.room-detail-panel {
  position: relative;
  width: 22rem;
  max-width: 94%;
  height: 100%;
  padding: 0.75rem;
  overflow: hidden auto;
  border-left: 1px solid var(--ui-panel-border);
  background: var(--ui-panel);
  box-shadow: var(--ui-panel-shadow);
}

.room-detail-panel:focus {
  outline: none;
}

.room-detail-head {
  display: flex;
  align-items: flex-start;
  justify-content: space-between;
  gap: 0.75rem;
  padding-bottom: 0.5rem;
  border-bottom: 1px solid var(--ui-panel-border);
}

.room-detail-eyebrow {
  color: var(--ui-panel-muted);
  font-size: var(--font-ui-eyebrow);
  letter-spacing: 0.04em;
  text-transform: uppercase;
}

.room-detail-head h2 {
  margin: 0.125rem 0 0;
  font-size: var(--font-ui-body);
  font-weight: 600;
}

.room-detail-body {
  display: flex;
  flex-direction: column;
  gap: 0.75rem;
  padding-top: 0.5rem;
}

.room-detail-section h3 {
  margin-bottom: 0.25rem;
  color: var(--ui-panel-muted);
  font-size: var(--font-ui-eyebrow);
  font-weight: 600;
  letter-spacing: 0.04em;
  text-transform: uppercase;
}

.room-detail-note,
.room-detail-hint {
  color: var(--ui-panel-muted);
  font-size: var(--font-ui-note);
}

.room-detail-hint {
  margin-top: 0.25rem;
}

.room-detail-error {
  margin-top: 0.25rem;
  color: var(--ac-write);
  font-size: var(--font-ui-note);
}

.room-detail-diagnostics {
  display: grid;
  gap: 0.25rem;
  margin: 0.25rem 0;
  padding: 0;
  list-style: none;
}

.room-detail-diagnostics li {
  display: flex;
  flex-wrap: wrap;
  gap: 0.375rem;
  font-size: var(--font-ui-note);
}

.room-detail-diagnostic-name {
  font-weight: 600;
}

.room-detail-diagnostic-state {
  color: var(--ui-panel-muted);
}

.room-detail-diagnostic-error {
  color: var(--ac-write);
}

.room-detail-members {
  display: flex;
  flex-wrap: wrap;
  gap: 0.25rem;
  margin: 0;
  padding: 0;
  list-style: none;
}

.room-detail-members li {
  padding: 0.0625rem 0.375rem;
  border: 1px solid var(--ui-panel-border);
  border-radius: 999px;
  color: var(--ui-panel-muted);
  font-size: var(--font-ui-note);
}
</style>
