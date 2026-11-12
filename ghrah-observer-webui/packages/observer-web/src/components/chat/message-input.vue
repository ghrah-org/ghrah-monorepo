<!--
SPDX-FileCopyrightText: 2026 chenxya <chenxya@ghrah.org>

SPDX-License-Identifier: Apache-2.0
-->

<script setup lang="ts">
import type { ChatBlock } from "@ghrah/observer-core";
import type { RoomMember } from "@ghrah/protocol";
import { computed, onBeforeUnmount, onMounted, ref, watch } from "vue";
import { useI18n } from "vue-i18n";
import AttachmentChip from "./attachment-chip.vue";
import UiButton from "@/components/ui/button.vue";
import UiIconButton from "@/components/ui/icon-button.vue";
import UiTextField from "@/components/ui/text-field.vue";
import { type AttachmentDeps, useAttachments } from "@/composables/useAttachments";

const { t } = useI18n();

const props = defineProps<{
  disabled?: boolean;
  /** 当前 room 的成员表（候选 target 上限）。 */
  members: RoomMember[];
  /** 附件上传所属项目（视频走附件，需要项目作用域）。 */
  projectId: string;
  /** 测试/嵌入式注入点；正常渲染不传。 */
  attachmentDeps?: AttachmentDeps;
}>();
const emit = defineEmits<{ send: [targets: string[], content: string, blocks: ChatBlock[]] }>();

const input = ref("");
const inputEl = ref<{ element: HTMLTextAreaElement | null } | null>(null);
/** 目标选择：恒至少一个 agent（无 agent 成员时为空，退回历史广播语义）。 */
const selectedTargets = ref<Set<string>>(new Set());
const targetMenuOpen = ref(false);
const targetRoot = ref<HTMLElement | null>(null);
const attachments = useAttachments({
  projectId: () => props.projectId,
  deps: props.attachmentDeps,
});
const fileInput = ref<HTMLInputElement | null>(null);
const dragging = ref(false);

/** autosize：内容变化时高度自适应；CSS max-h-40 封顶后转滚动。
 *  happy-dom 无真实布局（scrollHeight=0），单测中此函数为无害空转。 */
function autosize() {
  const el = inputEl.value?.element;
  if (!el) return;
  el.style.height = "auto";
  const style = getComputedStyle(el);
  const borderHeight = parseFloat(style.borderTopWidth) + parseFloat(style.borderBottomWidth);
  el.style.height = `${el.scrollHeight + borderHeight}px`;
}

watch(input, autosize, { flush: "post" });

/** 键位接管：textarea 的 Enter 不再触发 form submit（HTML 规范仅单行输入隐式提交）。
 *  Enter（无 Shift、非 IME 组合期）→ 发送；Shift+Enter → 默认插入换行；
 *  Ctrl/Cmd+Enter 顺带覆盖（保持旧快捷键）；isComposing 期间忽略，保证中文候选词确认。 */
function onKeydown(e: KeyboardEvent) {
  if (e.key !== "Enter") return;
  if (e.isComposing) return;
  if (e.shiftKey) return;
  e.preventDefault();
  handleSubmit();
}

/** 候选 = 当前 room 的 agent 成员（定向范围不超出本 room）。 */
const memberAgents = computed(() =>
  props.members
    .filter((member) => member.subject_type === "agent")
    .map((member) => ({ id: member.subject, name: member.subject_name || member.subject })),
);

const memberIdsKey = computed(() => memberAgents.value.map((agent) => agent.id).join("|"));

/** 默认选中第一个 agent；成员变化时保留仍有效的选择，失效则回到第一个。 */
watch(
  memberIdsKey,
  () => {
    const valid = new Set(memberAgents.value.map((agent) => agent.id));
    const kept = [...selectedTargets.value].filter((id) => valid.has(id));
    selectedTargets.value = new Set(
      kept.length > 0 ? kept : memberAgents.value[0] ? [memberAgents.value[0].id] : [],
    );
  },
  { immediate: true },
);

const allTargetsSelected = computed(
  () => memberAgents.value.length > 0 && selectedTargets.value.size === memberAgents.value.length,
);
const targetLabel = computed(() => {
  // 单选优先显示 @name（只有一个 agent 时与「全部」等价，但 @name 更具体）。
  if (selectedTargets.value.size === 1) {
    const id = [...selectedTargets.value][0];
    const name = memberAgents.value.find((agent) => agent.id === id)?.name ?? id;
    return `@${name}`;
  }
  if (allTargetsSelected.value) return t("chat.input.targetAll");
  return t("chat.input.targetCount", { count: selectedTargets.value.size });
});

function toggleTarget(id: string) {
  const next = new Set(selectedTargets.value);
  if (next.has(id)) {
    // 至少保留一个目标：取消最后一个时忽略，避免退回广播语义。
    if (next.size <= 1) return;
    next.delete(id);
  } else {
    next.add(id);
  }
  selectedTargets.value = next;
}

function onTargetCheckbox(id: string, event: Event) {
  const target = event.target as HTMLInputElement;
  const next = new Set(selectedTargets.value);
  if (next.has(id)) {
    if (next.size <= 1) {
      target.checked = true;
      return;
    }
    next.delete(id);
  } else {
    next.add(id);
  }
  selectedTargets.value = next;
}

function selectAllTargets() {
  selectedTargets.value = new Set(memberAgents.value.map((agent) => agent.id));
}

/** 取消全选 = 回到默认的第一个 agent（发送目标恒非空）。 */
function clearAllTargets() {
  const first = memberAgents.value[0];
  selectedTargets.value = new Set(first ? [first.id] : []);
}

function closeTargetMenu() {
  targetMenuOpen.value = false;
}

function onDocumentPointerDown(event: Event) {
  if (!targetMenuOpen.value) return;
  const target = event.target;
  if (target instanceof Node && targetRoot.value?.contains(target)) return;
  closeTargetMenu();
}

function onDocumentKeydown(event: KeyboardEvent) {
  if (event.key === "Escape" && targetMenuOpen.value) closeTargetMenu();
}

onMounted(() => {
  document.addEventListener("pointerdown", onDocumentPointerDown);
  document.addEventListener("keydown", onDocumentKeydown);
});
onBeforeUnmount(() => {
  document.removeEventListener("pointerdown", onDocumentPointerDown);
  document.removeEventListener("keydown", onDocumentKeydown);
});

// @-补全：输入以 @ 开头时弹出过滤列表
const mentionQuery = computed(() => {
  const v = input.value;
  if (!v.startsWith("@")) return null;
  const rest = v.slice(1);
  // 仅当尚未空白符分隔（仍在输入 name 阶段）时弹补全；换行与空格同义（多行输入场景）
  if (/\s/.test(rest)) return null;
  return rest.toLowerCase();
});

const mentionCandidates = computed(() => {
  const q = mentionQuery.value;
  if (q === null) return [];
  return memberAgents.value.filter((agent) => agent.name.toLowerCase().includes(q));
});

function pickMention(id: string) {
  toggleTarget(id);
  input.value = "";
}

// 解析前导 @<name> token（一个或多个，可连写如 @B@C 或以空格/结尾分隔），追加进 targets 并从 content 剥离。
// 语义：前导 @ 与下拉选择合并去重；无前导 @ 时 content=raw；有前导 @ 时 content=剥离后剩余。
function parseLeadingMentions(text: string): { targets: string[]; content: string } {
  const targets = new Set<string>();
  let rest = text;
  // @name 后可跟：空白、下一个 @、或字符串结尾
  const re = /^@([^\s@]+)(?:\s+|(?=@)|$)/;
  let m = rest.match(re);
  while (m) {
    const name = m[1];
    const member = memberAgents.value.find((agent) => agent.name === name);
    if (member) {
      targets.add(member.id);
      // 剥离整个匹配（含分隔符）；若分隔符是 \s+，content 去掉该空白
      rest = rest.slice(m[0].length);
      m = rest.match(re);
    } else {
      break;
    }
  }
  return { targets: [...targets], content: rest.trim() };
}

const canSend = computed(
  () =>
    !props.disabled &&
    !attachments.busy.value &&
    (input.value.trim().length > 0 || attachments.hasReadyBlocks.value),
);

function handleSubmit() {
  if (!canSend.value) return;
  const raw = input.value.trim();
  const blocks = attachments.blocks.value;
  if (!raw && blocks.length === 0) return;
  const parsed = parseLeadingMentions(raw);
  const targets = new Set(selectedTargets.value);
  for (const t of parsed.targets) targets.add(t);
  // 有 agent 成员时发送目标恒非空（默认第一个 agent）；无成员时保留历史广播语义。
  if (targets.size === 0 && memberAgents.value[0]) targets.add(memberAgents.value[0].id);
  // 有前导 @ 被解析时 content 取剥离后剩余；否则 content 取原文。
  const content = parsed.targets.length > 0 ? parsed.content : raw;
  if (!content && blocks.length === 0) return;
  emit("send", [...targets], content, blocks);
  input.value = "";
  selectedTargets.value = new Set(memberAgents.value[0] ? [memberAgents.value[0].id] : []);
  targetMenuOpen.value = false;
  attachments.clear();
}

function onFileChange(event: Event) {
  const target = event.target as HTMLInputElement;
  if (target.files && target.files.length > 0) void attachments.addFiles(target.files);
  // 清空 value：同一文件连续选择两次仍会触发 change。
  target.value = "";
}

function onDragOver(event: DragEvent) {
  if (props.disabled) return;
  const types = event.dataTransfer?.types;
  if (types && Array.from(types).includes("Files")) dragging.value = true;
}

function onDragLeave() {
  dragging.value = false;
}

function onDrop(event: DragEvent) {
  dragging.value = false;
  if (props.disabled) return;
  const files = event.dataTransfer?.files;
  if (files && files.length > 0) void attachments.addFiles(files);
}

/** 粘贴图片：剪贴板同时带纯文本时放行文本粘贴，只拦「纯文件/截图」场景。 */
function onPaste(event: ClipboardEvent) {
  if (props.disabled) return;
  const text = event.clipboardData?.getData("text/plain");
  if (text) return;
  const files = event.clipboardData?.files;
  if (files && files.length > 0) {
    event.preventDefault();
    void attachments.addFiles(files);
  }
}

const selectedTargetNames = computed(() =>
  [...selectedTargets.value]
    .map((id) => memberAgents.value.find((agent) => agent.id === id)?.name ?? id)
    .filter(Boolean),
);

const placeholder = computed(() =>
  selectedTargetNames.value.length > 0
    ? t("chat.input.toTarget", { name: selectedTargetNames.value.join(", ") })
    : t("chat.input.broadcast"),
);
</script>

<template>
  <form
    class="chat-composer flex flex-col gap-2 p-3"
    :class="{ 'chat-composer-dragging': dragging }"
    @submit.prevent="handleSubmit"
    @dragover.prevent="onDragOver"
    @dragleave.prevent="onDragLeave"
    @drop.prevent="onDrop"
  >
    <!-- 附件缩略图与错误提示（保持在输入行上方） -->
    <div
      v-if="attachments.items.value.length > 0 || attachments.errors.value.length > 0"
      class="chat-attachments"
    >
      <AttachmentChip
        v-for="item in attachments.items.value"
        :key="item.id"
        :attachment="item"
        @remove="attachments.remove(item.id)"
        @retry="attachments.retry(item.id)"
      />
      <p
        v-for="(key, index) in attachments.errors.value"
        :key="`${key}-${index}`"
        class="chat-attachment-error"
        role="alert"
      >
        {{ t(key) }}
      </p>
    </div>

    <!-- 正文输入：占满整行，左侧与容器内边距对齐 -->
    <div class="chat-input-row">
      <div class="chat-input-field">
        <UiTextField
          ref="inputEl"
          v-model="input"
          multiline
          variant="chat"
          rows="1"
          :disabled="props.disabled"
          class="chat-input-text"
          :placeholder="placeholder"
          @keydown="onKeydown"
          @paste="onPaste"
        />
        <!-- @-补全下拉 -->
        <ul v-if="mentionCandidates.length > 0" class="chat-mention-list">
          <li
            v-for="agent in mentionCandidates"
            :key="agent.id"
            class="chat-mention-item"
            @mousedown.prevent="pickMention(agent.id)"
          >
            @{{ agent.name }}
          </li>
        </ul>
      </div>
    </div>

    <!-- 工具栏：左侧目标选择与附件，右侧发送 -->
    <div class="chat-toolbar">
      <input
        ref="fileInput"
        class="chat-file-input"
        type="file"
        accept="image/*,video/*,.pdf,.txt,.md,.csv,.json,.doc,.docx,.xls,.xlsx,.zip"
        multiple
        :disabled="props.disabled"
        @change="onFileChange"
      />

      <div v-if="memberAgents.length > 0" ref="targetRoot" class="chat-target">
        <button
          type="button"
          class="chat-target-trigger"
          :disabled="props.disabled"
          :aria-expanded="targetMenuOpen"
          aria-haspopup="true"
          aria-controls="chat-target-menu"
          :aria-label="t('chat.input.targets')"
          @click="targetMenuOpen = !targetMenuOpen"
        >
          <span class="chat-target-label">{{ targetLabel }}</span>
          <span class="chat-target-caret" aria-hidden="true">▾</span>
        </button>
        <div
          v-if="targetMenuOpen"
          id="chat-target-menu"
          class="chat-target-menu"
          role="group"
          :aria-label="t('chat.input.targets')"
        >
          <div class="chat-target-actions">
            <button type="button" class="chat-target-action" @click="selectAllTargets">
              {{ t("chat.input.selectAll") }}
            </button>
            <button type="button" class="chat-target-action" @click="clearAllTargets">
              {{ t("chat.input.clearAll") }}
            </button>
          </div>
          <label v-for="agent in memberAgents" :key="agent.id" class="chat-target-option">
            <input
              type="checkbox"
              :checked="selectedTargets.has(agent.id)"
              @change="onTargetCheckbox(agent.id, $event)"
            />
            <span>@{{ agent.name }}</span>
          </label>
        </div>
      </div>

      <UiIconButton
        variant="ghost"
        size="sm"
        :label="t('chat.input.attach')"
        :disabled="props.disabled"
        @click="fileInput?.click()"
      >
        <svg class="chat-attach-icon" viewBox="0 0 16 16" aria-hidden="true">
          <path
            d="M8 3v10M3 8h10"
            fill="none"
            stroke="currentColor"
            stroke-width="1.5"
            stroke-linecap="round"
          />
        </svg>
      </UiIconButton>

      <div class="chat-toolbar-spacer" />

      <UiButton type="submit" :disabled="!canSend" variant="primary" size="sm">
        {{ t("chat.input.send") }}
      </UiButton>
    </div>
  </form>
</template>

<style scoped>
/* 整框只留一层圆角 surface：去掉 Uno 快捷方式的投影，focus-within 给极轻描边。 */
.chat-composer {
  border: 1px solid var(--color-border);
  box-shadow: none;
  transition:
    border-color 120ms ease,
    box-shadow 120ms ease;
}

.chat-composer:focus-within {
  border-color: var(--color-border-strong);
  box-shadow: 0 0 0 1px var(--color-border-strong);
}

/* textarea 去边框/焦点环/背景：焦点提示由外层 focus-within 统一表达。 */
.chat-composer :deep(textarea) {
  border: 0;
  background: transparent;
  box-shadow: none;
  scrollbar-width: thin;
  scrollbar-color: var(--color-border-strong) transparent;
}

.chat-composer :deep(textarea:focus) {
  outline: none;
  box-shadow: none;
}

.chat-input-row {
  display: flex;
  min-width: 0;
}

.chat-input-field {
  position: relative;
  display: flex;
  min-width: 0;
  flex: 1;
}

/* 正文从容器内边距起步，与左侧工具栏对齐；边框/背景已由上一条 :deep 规则去掉。 */
.chat-input-field :deep(textarea) {
  flex: 1;
  padding-left: 0;
  padding-right: 0;
}

.chat-toolbar {
  display: flex;
  min-width: 0;
  align-items: center;
  gap: 0.375rem;
}

.chat-toolbar-spacer {
  flex: 1;
}

.chat-file-input {
  display: none;
}

.chat-attach-icon {
  display: block;
  width: 1rem;
  height: 1rem;
}

.chat-target {
  position: relative;
  flex: none;
}

.chat-target-trigger {
  display: inline-flex;
  align-items: center;
  gap: 0.25rem;
  max-width: 10rem;
  padding: 0.25rem 0.5rem;
  border: 1px solid var(--color-border);
  border-radius: var(--radius-md);
  background: var(--color-surface-2);
  color: var(--color-text-muted);
  font-size: var(--font-ui-note);
  cursor: pointer;
}

.chat-target-trigger:hover {
  border-color: var(--color-border-strong);
  color: var(--color-text);
}

.chat-target-label {
  overflow: hidden;
  text-overflow: ellipsis;
  white-space: nowrap;
}

.chat-target-caret {
  flex: none;
}

.chat-target-menu {
  position: absolute;
  bottom: calc(100% + 0.25rem);
  left: 0;
  z-index: 20;
  display: flex;
  flex-direction: column;
  gap: 0.25rem;
  min-width: 11rem;
  max-height: 14rem;
  padding: 0.375rem;
  overflow-y: auto;
  border: 1px solid var(--color-border-strong);
  border-radius: var(--radius-md);
  background: var(--color-surface);
  box-shadow: var(--ui-panel-shadow);
}

.chat-target-actions {
  display: flex;
  gap: 0.5rem;
  padding-bottom: 0.25rem;
  border-bottom: 1px solid var(--color-border);
}

.chat-target-action {
  padding: 0;
  border: 0;
  background: none;
  color: var(--color-text-muted);
  font-size: var(--font-ui-note);
  text-decoration: underline;
  cursor: pointer;
}

.chat-target-option {
  display: flex;
  align-items: center;
  gap: 0.375rem;
  padding: 0.125rem 0.25rem;
  border-radius: var(--radius-md);
  color: var(--color-text);
  font-size: var(--font-ui-note);
  cursor: pointer;
}

.chat-target-option:hover {
  background: var(--color-surface-2);
}

.chat-attachments {
  display: flex;
  flex-wrap: wrap;
  gap: 0.5rem;
}

.chat-attachment-error {
  margin: 0;
  color: var(--color-danger);
  font-size: var(--font-ui-note);
}

.chat-composer-dragging {
  outline: 1px dashed var(--color-border-strong);
  outline-offset: -0.25rem;
}
</style>
