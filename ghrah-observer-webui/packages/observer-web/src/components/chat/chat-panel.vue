<!--
SPDX-FileCopyrightText: 2026 chenxya <chenxya@ghrah.org>

SPDX-License-Identifier: Apache-2.0
-->

<script setup lang="ts">
import type { ChatBlock } from "@ghrah/observer-core";
import {
  useAgentsStore,
  useChatStore,
  useConnectionStore,
  useRoomsStore,
  useTranscriptStore,
} from "@ghrah/observer-core";
import { computed, nextTick, onScopeDispose, ref, watch } from "vue";
import { useI18n } from "vue-i18n";
import {
  CONVERSATION_WIDTH_DEFAULT_REM,
  CONVERSATION_WIDTH_MAX_REM,
  CONVERSATION_WIDTH_MIN_REM,
  CONVERSATION_WIDTH_STEP_REM,
  conversationWidthRem,
  previewConversationWidth,
  setConversationWidth,
} from "@/composables/useConversationWidth";
import { useObserver } from "@/composables/useObserver";
import { useRoomMemberLabel } from "@/composables/useRoomMemberLabel";
import { useTabScrollRestore } from "@/composables/useTabScrollRestore";
import { useWorkspaceColumns } from "@/composables/useWorkspaceColumns";
import RoomConversation from "@/components/room/room-conversation.vue";
import ConversationResizer from "./conversation-resizer.vue";
import MessageInput from "./message-input.vue";
import RoomDetailDrawer, { type DeliveryDiagnostic } from "./room-detail-drawer.vue";
import UiButton from "@/components/ui/button.vue";

const props = defineProps<{
  projectId: string;
  roomId: string;
}>();

const emit = defineEmits<{
  roomInvalidated: [target: { projectId: string; roomId: string }];
}>();

const chat = useChatStore();
const agents = useAgentsStore();
const rooms = useRoomsStore();
const transcript = useTranscriptStore();
const connection = useConnectionStore();
const { roomSend, refreshRoom } = useObserver();
const refreshing = ref(false);
const deliveryError = ref("");
async function refreshDeliveries() {
  if (refreshing.value || connection.state !== "connected") return;
  const target = { projectId: props.projectId, roomId: props.roomId };
  refreshing.value = true;
  deliveryError.value = "";
  try {
    await refreshRoom(target);
  } catch (error) {
    if (props.projectId === target.projectId && props.roomId === target.roomId) {
      deliveryError.value = String(error);
    }
  } finally {
    refreshing.value = false;
  }
}
const { t } = useI18n();
const memberLabel = useRoomMemberLabel();

const messageContainer = ref<HTMLElement | null>(null);

// 会话区容器：只用于量出可用宽度，手柄也挂在它上面定位。
const conversationArea = ref<HTMLElement | null>(null);
const columnMode = useWorkspaceColumns();
/** 可用宽度（rem）；0 表示尚未测量。 */
const availableRem = ref(0);
/** 手柄要让出的总宽度：列两侧各留一格，手柄只占其中靠近列的一小段。 */
const RESIZER_GUTTER_REM = 3;

// 只在宽屏可调：窄屏下列宽本就该占满，给出抓手只会诱导用户把自己的
// 阅读区拖成一条缝。exclusive（<1024px）与 rail 两档都不放开。
const resizable = computed(
  () =>
    (columnMode.value === "mid" || columnMode.value === "wide") &&
    availableRem.value > CONVERSATION_WIDTH_MIN_REM + RESIZER_GUTTER_REM,
);

// 上限随可用宽度收缩，保证列永远不会吃掉手柄的位置：这样拖拽途中手柄
// 不会突然消失（那会打断指针捕获），也不会压住列的滚动条。
const maxWidthRem = computed(() =>
  Math.max(
    CONVERSATION_WIDTH_MIN_REM,
    Math.min(CONVERSATION_WIDTH_MAX_REM, availableRem.value - RESIZER_GUTTER_REM),
  ),
);

// 可用宽度随窗口与字号变化，交给容器自身的尺寸变化驱动。
let areaObserver: ResizeObserver | null = null;
watch(conversationArea, (area) => {
  areaObserver?.disconnect();
  areaObserver = null;
  if (!area) {
    availableRem.value = 0;
    return;
  }
  const measure = () => {
    const root = Number.parseFloat(getComputedStyle(document.documentElement).fontSize);
    availableRem.value = Number.isFinite(root) && root > 0 ? area.clientWidth / root : 0;
  };
  measure();
  if (typeof ResizeObserver === "function") {
    areaObserver = new ResizeObserver(measure);
    areaObserver.observe(area);
  }
});
onScopeDispose(() => {
  areaObserver?.disconnect();
  areaObserver = null;
});

/** 视口距底部在该阈值内视为"贴底"，新消息到达时才自动跟随滚底。 */
const NEAR_BOTTOM_PX = 40;
const sticky = ref(true);

const room = computed(() => rooms.rooms.get(props.roomId) ?? null);

const roomEntries = computed(() => transcript.entriesOf(props));
const outgoingCount = computed(
  () =>
    chat.entries.filter(
      (entry) => entry.projectId === props.projectId && entry.roomId === props.roomId,
    ).length,
);
const messageCount = computed(() => roomEntries.value.length + outgoingCount.value);
const turnCount = computed(() => transcript.room(props)?.turns.size ?? 0);

const canChat = computed(() => room.value !== null && connection.state === "connected");

/** 详情抽屉：运维信息（投递状态、协议错误、Room 管理）按需展开。 */
const detailOpen = ref(false);
watch(
  () => props.roomId,
  () => {
    detailOpen.value = false;
  },
);

const deliveryDiagnostics = computed<DeliveryDiagnostic[]>(() =>
  roomEntries.value.flatMap((entry) =>
    (entry.deliveries ?? [])
      .filter((delivery) => delivery.state !== "completed")
      .map((delivery) => ({
        id: delivery.delivery_id,
        recipient: displayName(delivery.recipient_id),
        state: delivery.state,
        detail: delivery.blocked_reason || delivery.error || undefined,
      })),
  ),
);

/** 会话头一行元信息：成员数 · 消息数 · 连接状态。 */
const roomMeta = computed(() => {
  const current = room.value;
  if (!current) return "";
  return t("chat.meta", {
    members: current.members.length,
    messages: messageCount.value,
    state: t(`app.status.${connection.state}`),
  });
});

async function handleSend(targets: string[], content: string, blocks: ChatBlock[] = []) {
  const projectId = props.projectId;
  const roomId = props.roomId;
  const currentRoom = room.value;
  if (!roomId || !projectId || !currentRoom) return;
  if (!content && blocks.length === 0) return;
  const target = { projectId, roomId };
  chat.clearBlockLoss(roomId);
  const pending = chat.addPendingEntry({
    ...target,
    to: roomId,
    content,
    agentName: "",
    targets,
    blocks,
  });
  roomSend(target, pending.clientSeq, content, targets, blocks)
    .then((r) => {
      if (r === null)
        chat.markPendingErrorBySeq(target, pending.clientSeq, t("chat.sendFailedDisconnected"));
      else if (r && !r.success)
        chat.markPendingErrorBySeq(target, pending.clientSeq, r.error ?? t("agentEntity.failed"));
    })
    .catch((e) =>
      chat.markPendingErrorBySeq(
        target,
        pending.clientSeq,
        t("chat.sendFailed", { msg: String(e ?? "") }),
      ),
    );
  sticky.value = true;
  await nextTick(() => scrollToBottom());
}

/** 旧核会静默忽略 content_blocks：服务端回读没带回媒体块时给出可见提示。 */
const blockLossNotice = computed(() => chat.blockLossRooms.has(props.roomId));

function scrollToBottom() {
  if (messageContainer.value) {
    messageContainer.value.scrollTop = messageContainer.value.scrollHeight;
  }
}

function isNearBottom(): boolean {
  const el = messageContainer.value;
  if (!el) return true;
  return el.scrollHeight - el.scrollTop - el.clientHeight <= NEAR_BOTTOM_PX;
}

// sticky 用户切回落新底；上翻用户切回回原位置，LRU 补拉瞬态靠 pending 重试
const { onScroll } = useTabScrollRestore(messageContainer, {
  contentKey: messageCount,
  target: () => (sticky.value ? "bottom" : null),
});

let applyingWidth = false;

function handleScroll(): void {
  // 调宽引起的滚动是程序式位置校正，不代表用户改变贴底/上翻意图。
  if (!applyingWidth) sticky.value = isNearBottom();
  onScroll();
}

function firstVisibleEntry(el: HTMLElement): { node: HTMLElement; offset: number } | null {
  const containerTop = el.getBoundingClientRect().top;
  for (const node of el.querySelectorAll<HTMLElement>("[data-room-item]")) {
    const rect = node.getBoundingClientRect();
    if (rect.bottom > containerTop) return { node, offset: rect.top - containerTop };
  }
  return null;
}

function restoreEntryAnchor(el: HTMLElement, anchor: { node: HTMLElement; offset: number }) {
  const containerTop = el.getBoundingClientRect().top;
  const delta = anchor.node.getBoundingClientRect().top - containerTop - anchor.offset;
  if (Math.abs(delta) > 0.5) el.scrollTop += delta;
}

/**
 * 调宽期间锁定纵向阅读位置：贴底时保持贴底，否则锚定第一条可见消息，
 * 宽度变化引起回流后把它放回原视口偏移，避免内容随拖拽上下漂移。
 */
function applyConversationWidth(value: number, commit: boolean) {
  const el = messageContainer.value;
  const keepBottom = sticky.value;
  const anchor = el && !keepBottom ? firstVisibleEntry(el) : null;
  applyingWidth = true;
  try {
    if (commit) setConversationWidth(value);
    else previewConversationWidth(value);
    if (!el) return;
    // 读一次 scrollHeight 强制回流，再按锚点校正位置。
    void el.scrollHeight;
    if (keepBottom) {
      el.scrollTop = el.scrollHeight;
      return;
    }
    if (anchor) restoreEntryAnchor(el, anchor);
  } finally {
    applyingWidth = false;
  }
}

function previewWidth(value: number): void {
  applyConversationWidth(value, false);
}

function commitWidth(value: number): void {
  applyConversationWidth(value, true);
}

function resetWidth(): void {
  applyConversationWidth(CONVERSATION_WIDTH_DEFAULT_REM, true);
}

// 贴底时新消息才自动跟随滚底；上翻浏览历史不被打断
watch([messageCount, turnCount], () => {
  if (sticky.value) nextTick(() => scrollToBottom());
});

/** 成员 ID → 显示名（subject_name / agents store / room 成员表回退原始值）。 */
function displayName(id: string): string {
  const projectId = props.projectId;
  const room = rooms.rooms.get(props.roomId);
  const member = room?.members.find((m) => m.subject === id);
  if (member) return memberLabel(member, projectId);
  return (
    agents.agentsForProject(projectId).find((a) => a.agentId === id || a.agentName === id)
      ?.agentName ?? id
  );
}
</script>

<template>
  <div class="relative flex flex-col h-full">
    <header class="room-header">
      <div class="room-header-main">
        <h3 class="room-header-title">{{ room ? room.name : t("chat.name") }}</h3>
        <p v-if="room" class="room-header-meta">{{ roomMeta }}</p>
      </div>
      <UiButton
        v-if="room"
        type="button"
        variant="secondary"
        :aria-expanded="detailOpen"
        aria-controls="room-detail-drawer"
        @click="detailOpen = !detailOpen"
      >
        {{ t("chat.detail.open") }}
      </UiButton>
    </header>

    <div
      v-if="!room"
      class="flex-1 flex items-center justify-center text-gray-400 dark:text-gray-600 text-sm"
    >
      {{ t("chat.selectRoom") }}
    </div>

    <div v-else ref="conversationArea" class="relative flex min-h-0 flex-1 flex-col">
      <div
        ref="messageContainer"
        class="chat-conversation flex-1 overflow-y-auto px-3 py-4"
        @scroll.passive="handleScroll"
      >
        <RoomConversation :key="room.room_id" :room-id="roomId" :project-id="projectId" />
      </div>

      <!-- 左右各一只手柄；窄屏列宽本就该占满，不放手柄以免诱导用户把自己拖窄。 -->
      <template v-if="resizable">
        <ConversationResizer
          side="left"
          :width="conversationWidthRem"
          :min="CONVERSATION_WIDTH_MIN_REM"
          :max="maxWidthRem"
          :step="CONVERSATION_WIDTH_STEP_REM"
          @preview="previewWidth"
          @commit="commitWidth"
          @reset="resetWidth"
        />
        <ConversationResizer
          side="right"
          :width="conversationWidthRem"
          :min="CONVERSATION_WIDTH_MIN_REM"
          :max="maxWidthRem"
          :step="CONVERSATION_WIDTH_STEP_REM"
          @preview="previewWidth"
          @commit="commitWidth"
          @reset="resetWidth"
        />
      </template>
    </div>

    <p v-if="blockLossNotice" class="chat-block-loss" role="status">
      {{ t("chat.sendFailedNoBlocks") }}
    </p>

    <MessageInput
      v-if="room"
      :disabled="!canChat"
      :members="room.members"
      :project-id="projectId"
      @send="handleSend"
    />

    <RoomDetailDrawer
      :open="detailOpen"
      :room-id="roomId"
      :diagnostics="deliveryDiagnostics"
      :refreshing="refreshing"
      :delivery-error="deliveryError"
      @close="detailOpen = false"
      @refresh="refreshDeliveries"
      @room-invalidated="emit('roomInvalidated', $event)"
    />
  </div>
</template>

<style scoped>
/*
 * 消息流滚动条：细、无轨道底色，滑块复用边框语义色，暗色模式自动跟随 token。
 *
 * max-width 用 min() 再收一道：窗口变窄时列宽自动退到「可用宽度 - 3rem」，
 * 既不会顶掉手柄的位置，也不会让手柄被挤出视野。3rem 与 chat-panel 里的
 * RESIZER_GUTTER_REM 对应（两侧各 1.5rem）。
 *
 * overflow-anchor 关掉浏览器自带的滚动锚定：拖宽时由 applyConversationWidth
 * 显式锚定第一条可见消息，避免两套锚定互相打架。
 */
.chat-conversation {
  display: flex;
  flex-direction: column;
  inline-size: 100%;
  margin-inline: auto;
  max-width: min(var(--width-conversation), calc(100% - 3rem));
  overflow-anchor: none;
  scrollbar-width: thin;
  scrollbar-color: var(--color-border-strong) transparent;
}

.room-header {
  display: flex;
  align-items: center;
  justify-content: space-between;
  gap: 0.75rem;
  padding: 0.375rem 0.75rem;
  border-bottom: 1px solid var(--ui-panel-border);
  min-height: 3rem;
}

.room-header-main {
  min-width: 0;
}

.room-header-title {
  margin: 0;
  font-size: var(--font-ui-body);
  font-weight: 600;
  white-space: nowrap;
  overflow: hidden;
  text-overflow: ellipsis;
}

.room-header-meta {
  margin: 0.0625rem 0 0;
  color: var(--ui-panel-muted);
  font-size: var(--font-ui-note);
  white-space: nowrap;
  overflow: hidden;
  text-overflow: ellipsis;
}

.chat-block-loss {
  margin: 0 0.75rem;
  padding: 0.5rem 0.75rem;
  border: 1px solid var(--color-border);
  border-radius: var(--radius-md);
  background: var(--color-surface-2);
  color: var(--color-danger);
  font-size: var(--font-ui-note);
}
</style>
