<script setup lang="ts">
import type { ChatEntry } from "@ghrah/observer-core";
import {
  roomLogToChatEntries,
  useAgentsStore,
  useChatStore,
  useConnectionStore,
  useRoomsStore,
} from "@ghrah/observer-core";
import type { ContentBlock } from "@ghrah/protocol";
import { computed, nextTick, onScopeDispose, ref, watch } from "vue";
import { useI18n } from "vue-i18n";
import { useMarkdown } from "@/composables/useMarkdown";
import { useObserver } from "@/composables/useObserver";
import { useRoomMemberLabel } from "@/composables/useRoomMemberLabel";
import { useTabScrollRestore } from "@/composables/useTabScrollRestore";
import MessageInput from "./message-input.vue";

const props = defineProps<{
  projectId: string;
  roomId: string;
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
const deliveryStates = new Set([
  "pending",
  "processing",
  "completed",
  "failed",
  "interrupted",
  "blocked",
]);
const { render: renderMarkdown } = useMarkdown();
const { t } = useI18n();
const memberLabel = useRoomMemberLabel();

const messageContainer = ref<HTMLElement | null>(null);

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

async function handleSend(targets: string[], content: string) {
  const projectId = props.projectId;
  const roomId = props.roomId;
  const currentRoom = room.value;
  if (!roomId || !projectId || !currentRoom || !content) return;
  const target = { projectId, roomId };
  chat.addPendingEntry({ projectId, to: roomId, content, agentName: "", roomId, targets });
  roomSend(roomId, content, targets)
    .then((r) => {
      if (r === null) chat.markPendingError(target, content, t("chat.sendFailedDisconnected"));
      else if (!r.success)
        chat.markPendingError(target, content, r.error ?? t("agentEntity.failed"));
      else void refreshDeliveries();
    })
    .catch((e) =>
      chat.markPendingError(target, content, t("chat.sendFailed", { msg: String(e ?? "") })),
    );
  sticky.value = true;
  await nextTick(() => scrollToBottom());
}

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

function handleScroll(): void {
  sticky.value = isNearBottom();
  onScroll();
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

function isText(block: ContentBlock): block is Extract<ContentBlock, { type: "text" }> {
  return block.type === "text";
}
</script>

<template>
  <div class="flex flex-col h-full">
    <div class="px-3 py-2">
      <button
        class="btn-secondary"
        :disabled="refreshing || connection.state !== 'connected'"
        @click="refreshDeliveries"
      >
        {{ t("agentEntity.deliveryRefresh") }}
      </button>
      <p class="text-xs">{{ t("agentEntity.deliveryHint") }}</p>
      <p v-if="deliveryError" role="alert">{{ deliveryError }}</p>
    </div>
    <div
      class="px-3 py-2 border-b border-gray-200 dark:border-gray-700 bg-gray-50 dark:bg-gray-900 flex items-center justify-between"
    >
      <h3 class="text-sm font-semibold truncate">
        {{ room ? t("chat.title", { room: room.name }) : t("chat.name") }}
      </h3>
    </div>

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

    <div
      v-else
      ref="messageContainer"
      class="flex-1 overflow-y-auto p-3 space-y-2"
      @scroll.passive="handleScroll"
    >
      <div
        v-for="entry in roomEntries"
        :key="`${entry.nodeId}-${entry.childSeq}`"
        :class="['chat-entry', entryClass(entry), { 'entry-pending': entry.pending }]"
      >
        <div class="entry-header">{{ entryHeader(entry) }}</div>

        <div
          v-for="delivery in entry.deliveries ?? []"
          :key="delivery.delivery_id"
          class="text-xs"
          role="status"
        >
          {{ displayName(delivery.recipient_id) }} ·
          {{
            t(
              `agentEntity.deliveryStates.${deliveryStates.has(delivery.state) ? delivery.state : "unknown"}`,
            )
          }}
          <p v-if="delivery.error || delivery.blocked_reason" role="alert">
            {{ delivery.blocked_reason || delivery.error }}
          </p>
        </div>
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
            <details v-else-if="block.type === 'tool_call'" class="block-tool">
              <summary class="text-xs">🔧 {{ block.name }}</summary>
              <pre class="text-xs whitespace-pre-wrap">{{ block.arguments }}</pre>
            </details>
            <details v-else-if="block.type === 'tool_result'" class="block-tool">
              <summary :class="['text-xs', block.success ? 'text-green-600' : 'text-red-600']">
                ↳ {{ block.name ?? block.tool_call_id }}{{ block.success ? "" : t("chat.failed") }}
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

    <MessageInput v-if="room" :disabled="!canChat" :members="room.members" @send="handleSend" />
  </div>
</template>

<style scoped>
.chat-entry {
  padding: 0.375rem 0.75rem;
  border-radius: 0.5rem;
  max-width: 85%;
  word-break: break-word;
}

.entry-human {
  background: #d1fae5;
  color: #065f46;
  align-self: flex-end;
  margin-left: auto;
}
:root.dark .entry-human {
  background: #064e3b;
  color: #a7f3d0;
}

.entry-conversation {
  background: #e8f0fe;
  color: #1a3a5c;
  align-self: flex-start;
}
:root.dark .entry-conversation {
  background: #1e3a5f;
  color: #b8d4f0;
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
</style>
