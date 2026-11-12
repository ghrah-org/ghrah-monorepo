<!--
SPDX-FileCopyrightText: 2026 chenxya <chenxya@ghrah.org>

SPDX-License-Identifier: Apache-2.0
-->

<script setup lang="ts">
import type { ChatBlock, ChatEntry } from "@ghrah/observer-core";
import {
  attachmentUrl,
  isVideoBlock,
  roomLogToChatEntries,
  useAgentsStore,
  useChatStore,
  useConnectionStore,
  useRoomsStore,
} from "@ghrah/observer-core";
import type { ContentBlock } from "@ghrah/protocol";
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
import { useMarkdown } from "@/composables/useMarkdown";
import { useObserver } from "@/composables/useObserver";
import { useRoomMemberLabel } from "@/composables/useRoomMemberLabel";
import { useTabScrollRestore } from "@/composables/useTabScrollRestore";
import { useWorkspaceColumns } from "@/composables/useWorkspaceColumns";
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
const connection = useConnectionStore();
const { roomSend, getRoomLog } = useObserver();
const refreshing = ref(false);
const deliveryError = ref("");
async function refreshDeliveries() {
  if (refreshing.value || connection.state !== "connected") return;
  refreshing.value = true;
  deliveryError.value = "";
  try {
    const result = await getRoomLog(props.roomId);
    if (result && !result.success) deliveryError.value = result.error ?? t("agentEntity.failed");
  } catch (error) {
    deliveryError.value = String(error);
  } finally {
    refreshing.value = false;
  }
}
const deliveryTimer = setInterval(() => {
  if (
    roomEntries.value.some((e) =>
      e.deliveries?.some((d) => ["pending", "processing"].includes(d.state)),
    )
  )
    void refreshDeliveries();
}, 3000);
onScopeDispose(() => clearInterval(deliveryTimer));
const { render: renderMarkdown } = useMarkdown();
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

/** 该 room 历史（room log 投影） + 本 room 的 pending/error 乐观层，按序合并。 */
const roomEntries = computed<ChatEntry[]>(() => {
  const projectId = props.projectId;
  const roomId = props.roomId;
  if (!projectId || !roomId) return [];
  const projectAgents = agents.agentsForProject(projectId);
  const history = [...(rooms.logs.get(roomId) ?? [])]
    .sort((a, b) => a.seq - b.seq)
    .map((entry) => {
      const agentId =
        entry.author_type === "agent"
          ? projectAgents.find(
              (agent) => agent.agentId === entry.author || agent.agentName === entry.author,
            )?.agentId
          : undefined;
      return roomLogToChatEntries(entry, { projectId, agentId });
    });
  const pending = chat.allEntries.filter(
    (e) =>
      (e.pending === true || e.error !== undefined) &&
      e.projectId === projectId &&
      e.roomId === roomId,
  );
  return [...history, ...pending];
});

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
    messages: roomEntries.value.length,
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
  const mediaBlockCount = blocks.filter((block) => block.type !== "text").length;
  chat.clearBlockLoss(roomId);
  chat.addPendingEntry({ projectId, to: roomId, content, agentName: "", roomId, targets, blocks });
  roomSend(roomId, content, targets, blocks)
    .then((r) => {
      if (r === null)
        chat.markPendingError(target, content, t("chat.sendFailedDisconnected"), mediaBlockCount);
      else if (!r.success)
        chat.markPendingError(target, content, r.error ?? t("agentEntity.failed"), mediaBlockCount);
      else void refreshDeliveries();
    })
    .catch((e) =>
      chat.markPendingError(
        target,
        content,
        t("chat.sendFailed", { msg: String(e ?? "") }),
        mediaBlockCount,
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
  contentKey: computed(() => roomEntries.value.length),
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
  for (const node of el.querySelectorAll<HTMLElement>(".chat-entry")) {
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
watch(roomEntries, () => {
  if (sticky.value) nextTick(() => scrollToBottom());
});

function entryClass(entry: ChatEntry): string {
  if (entry.error) return "entry-error";
  switch (entry.kind) {
    case "human_input":
      return "entry-human";
    case "conversation":
      return "entry-conversation";
    case "send_message":
      return "entry-send";
    case "broadcast":
      return "entry-broadcast";
    case "end_task":
      return "entry-endtask";
    default:
      return "entry-conversation";
  }
}

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

function entryHeader(entry: ChatEntry): string {
  const targetSuffix =
    entry.targets && entry.targets.length > 0
      ? ` → ${entry.targets.map((t) => `@${displayName(t)}`).join(" ")}`
      : "";
  switch (entry.kind) {
    case "human_input":
      return `${t("chat.header.you")}${targetSuffix}`;
    case "conversation":
      return `@${displayName(entry.from)}${targetSuffix}`;
    case "send_message":
      return `@${displayName(entry.from)} → @${displayName(entry.to)}`;
    case "broadcast":
      return `@${displayName(entry.from)} → @${t("chat.header.all")}`;
    case "end_task":
      return `✓ @${displayName(entry.from)}`;
    default:
      return `@${displayName(entry.from)}`;
  }
}

function imgSrc(block: Extract<ContentBlock, { type: "image" }>): string {
  if (block.url) return block.url;
  if (block.base64) return `data:${block.mime_type ?? "image/png"};base64,${block.base64}`;
  return "";
}

function audioSrc(block: Extract<ContentBlock, { type: "audio" }>): string {
  return `data:${block.mime_type};base64,${block.data}`;
}

function fileHref(block: Extract<ContentBlock, { type: "file" }>): string {
  if (block.url) return block.url;
  if (block.base64)
    return `data:${block.mime_type ?? "application/octet-stream"};base64,${block.base64}`;
  return "";
}

function fileLabel(block: Extract<ContentBlock, { type: "file" }>): string {
  return block.filename ?? "file";
}

function isText(block: ChatBlock): block is Extract<ContentBlock, { type: "text" }> {
  return block.type === "text";
}

function videoPoster(block: Extract<ChatBlock, { type: "video" }>): string {
  const frame = block.frames?.[0];
  if (!frame?.base64) return "";
  return `data:${frame.mime_type ?? "image/jpeg"};base64,${frame.base64}`;
}

function videoSrc(block: Extract<ChatBlock, { type: "video" }>): string {
  if (block.attachment_id) return attachmentUrl(props.projectId, block.attachment_id);
  return block.url ?? "";
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

    <div
      v-else-if="roomEntries.length === 0"
      class="flex-1 flex items-center justify-center text-gray-400 dark:text-gray-600 text-sm"
    >
      {{ t("chat.empty") }}
    </div>

    <div v-else ref="conversationArea" class="relative flex min-h-0 flex-1 flex-col">
      <div
        ref="messageContainer"
        class="chat-conversation flex-1 overflow-y-auto px-3 py-4 space-y-3"
        @scroll.passive="handleScroll"
      >
        <div
          v-for="entry in roomEntries"
          :key="`${entry.nodeId}-${entry.childSeq}`"
          :class="['chat-entry', entryClass(entry), { 'entry-pending': entry.pending }]"
        >
          <div class="entry-header">{{ entryHeader(entry) }}</div>

          <div v-if="entry.error" class="block-error">
            <span class="text-sm text-red-600 dark:text-red-400">{{ entry.error }}</span>
          </div>

          <!-- 富块渲染 -->
          <template v-else-if="entry.blocks && entry.blocks.length > 0">
            <div
              v-for="(block, j) in entry.blocks"
              :key="j"
              :class="['entry-block', `block-${block.type}`]"
            >
              <div v-if="isText(block)" class="markdown-body" v-html="renderMarkdown(block.text)" />
              <details v-else-if="block.type === 'reasoning'" class="block-reasoning">
                <summary class="text-xs italic text-gray-500 dark:text-gray-400">
                  {{ t("chat.reasoning") }}{{ block.incomplete ? "…" : "" }}
                </summary>
                <pre class="text-xs whitespace-pre-wrap">{{ block.reasoning }}</pre>
              </details>
              <img
                v-else-if="block.type === 'image'"
                :src="imgSrc(block)"
                alt="image"
                class="max-w-full rounded"
              />
              <audio
                v-else-if="block.type === 'audio'"
                :src="audioSrc(block)"
                controls
                class="w-full"
              />
              <a
                v-else-if="block.type === 'file'"
                :href="fileHref(block)"
                :download="fileLabel(block)"
                class="text-xs text-blue-600 dark:text-blue-400 underline"
              >
                📎 {{ fileLabel(block) }}
              </a>
              <template v-else-if="isVideoBlock(block)">
                <video
                  v-if="videoSrc(block)"
                  :src="videoSrc(block)"
                  :poster="videoPoster(block) || undefined"
                  controls
                  preload="metadata"
                  class="max-w-full rounded"
                />
                <div v-else class="block-video-fallback">
                  <img
                    v-if="videoPoster(block)"
                    :src="videoPoster(block)"
                    :alt="block.filename ?? t('chat.video')"
                    class="max-w-full rounded"
                  />
                  <span class="block-video-error">
                    {{ t("chat.videoUnavailable", { name: block.filename ?? t("chat.video") }) }}
                  </span>
                </div>
              </template>
              <details v-else-if="block.type === 'tool_call'" class="block-tool">
                <summary class="text-xs">🔧 {{ block.name }}</summary>
                <pre class="text-xs whitespace-pre-wrap">{{ block.arguments }}</pre>
              </details>
              <details v-else-if="block.type === 'tool_result'" class="block-tool">
                <summary :class="['text-xs', block.success ? 'text-green-600' : 'text-red-600']">
                  ↳ {{ block.name ?? block.tool_call_id
                  }}{{ block.success ? "" : t("chat.failed") }}
                </summary>
                <pre class="text-xs whitespace-pre-wrap">{{ block.content }}</pre>
                <pre v-if="block.error" class="text-xs text-red-500 whitespace-pre-wrap">{{
                  block.error
                }}</pre>
              </details>
              <div v-else-if="block.type === 'error'" class="block-error">
                <span class="text-xs text-red-600 dark:text-red-400"
                  >{{ block.error_type }}: {{ block.message }}</span
                >
              </div>
            </div>
          </template>

          <!-- 无块：纯 content（与富块 text 同走 Markdown 渲染） -->
          <div v-else class="markdown-body" v-html="renderMarkdown(entry.content)" />
        </div>
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

.chat-entry {
  font-size: var(--font-ui-body);
  line-height: var(--leading-body);
  padding: var(--spacing-2) var(--spacing-3);
  border-radius: var(--radius-md);
  max-width: 95%;
  word-break: break-word;
}

.entry-human {
  background: var(--color-chat-human);
  color: var(--color-chat-human-text);
  max-width: 90%;
  align-self: flex-end;
  margin-left: auto;
}

.entry-conversation {
  background: var(--color-chat-agent);
  color: var(--color-chat-agent-text);
  align-self: flex-start;
}

.entry-send {
  background: #f3f4f6;
  color: #4b5563;
  align-self: flex-start;
  margin-left: 2rem;
  border-left: 2px solid #9ca3af;
}
:root.dark .entry-send {
  background: #1f2937;
  color: #9ca3af;
}

.entry-broadcast {
  background: #fef3c7;
  color: #92400e;
  align-self: center;
  margin: 0 auto;
  border: 1px dashed #d97706;
}
:root.dark .entry-broadcast {
  background: #422006;
  color: #fde68a;
}

.entry-endtask {
  background: transparent;
  color: #6b7280;
  font-style: italic;
  align-self: flex-start;
}
:root.dark .entry-endtask {
  color: #9ca3af;
}

.entry-pending {
  opacity: 0.55;
  animation: pulse 1.5s ease-in-out infinite;
}
@keyframes pulse {
  0%,
  100% {
    opacity: 0.55;
  }
  50% {
    opacity: 0.85;
  }
}

.entry-error {
  background: #fee2e2;
  color: #991b1b;
  align-self: flex-end;
  margin-left: auto;
}
:root.dark .entry-error {
  background: #7f1d1d;
  color: #fecaca;
}

.block-error {
  margin-top: 0.125rem;
}

.entry-header {
  font-size: 0.8125rem;
  font-weight: 600;
  margin-bottom: 0.125rem;
  opacity: 0.8;
}

.entry-block {
  margin-top: 0.125rem;
}

.block-reasoning,
.block-tool {
  margin: 0.125rem 0;
}

.markdown-body :deep(p) {
  margin: 0.125rem 0;
}
.markdown-body :deep(pre) {
  background: rgba(0, 0, 0, 0.05);
  padding: 0.25rem 0.5rem;
  border-radius: 0.25rem;
  font-size: 0.875rem;
  overflow-x: auto;
}
:root.dark .markdown-body :deep(pre) {
  background: rgba(0, 0, 0, 0.3);
}

/* 列表：UnoCSS preflight 会重置 list-style 与缩进，此处恢复 */
.markdown-body :deep(ul) {
  margin: 0.25rem 0;
  padding-left: 1.25rem;
  list-style-type: disc;
}
.markdown-body :deep(ol) {
  margin: 0.25rem 0;
  padding-left: 1.25rem;
  list-style-type: decimal;
}
.markdown-body :deep(li) {
  margin: 0.125rem 0;
}

/* 行内 code（pre 内的 code 由 pre 自身提供背景，去重） */
.markdown-body :deep(code) {
  background: rgba(0, 0, 0, 0.06);
  border-radius: 0.25rem;
  padding: 0 0.25rem;
  font-size: 0.875rem;
}
.markdown-body :deep(pre code) {
  background: transparent;
  padding: 0;
}
:root.dark .markdown-body :deep(code) {
  background: rgba(255, 255, 255, 0.12);
}
:root.dark .markdown-body :deep(pre code) {
  background: transparent;
}

.markdown-body :deep(a) {
  text-decoration: underline;
}

.markdown-body :deep(h1),
.markdown-body :deep(h2),
.markdown-body :deep(h3),
.markdown-body :deep(h4) {
  font-weight: 600;
  margin: 0.25rem 0;
}
.markdown-body :deep(h1) {
  font-size: 1.125rem;
}
.markdown-body :deep(h2) {
  font-size: 1rem;
}
.markdown-body :deep(h3),
.markdown-body :deep(h4) {
  font-size: 0.9375rem;
}

.markdown-body :deep(blockquote) {
  margin: 0.25rem 0;
  padding-left: 0.5rem;
  border-left: 3px solid rgba(0, 0, 0, 0.15);
}
:root.dark .markdown-body :deep(blockquote) {
  border-left-color: rgba(255, 255, 255, 0.2);
}

.markdown-body :deep(table) {
  border-collapse: collapse;
  margin: 0.25rem 0;
  font-size: 0.875rem;
}
.markdown-body :deep(th),
.markdown-body :deep(td) {
  border: 1px solid rgba(0, 0, 0, 0.15);
  padding: 0.25rem 0.5rem;
}
:root.dark .markdown-body :deep(th),
:root.dark .markdown-body :deep(td) {
  border-color: rgba(255, 255, 255, 0.2);
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

.block-video-fallback {
  display: flex;
  flex-direction: column;
  gap: 0.25rem;
}

.block-video-error {
  color: var(--color-danger);
  font-size: var(--font-ui-note);
}
</style>
