<!--
SPDX-FileCopyrightText: 2026 chenxya <chenxya@ghrah.org>

SPDX-License-Identifier: Apache-2.0
-->

<script setup lang="ts">
import type { RoomMember } from "@ghrah/protocol";
import { computed, ref, watch } from "vue";
import { useI18n } from "vue-i18n";
import UiButton from "@/components/ui/button.vue";
import UiTextField from "@/components/ui/text-field.vue";

const { t } = useI18n();

const props = defineProps<{
  disabled?: boolean;
  /** 当前 room 的成员表（候选 target 上限）。 */
  members: RoomMember[];
}>();
const emit = defineEmits<{ send: [targets: string[], content: string] }>();

const input = ref("");
const inputEl = ref<{ element: HTMLTextAreaElement | null } | null>(null);
const selectedTargets = ref<Set<string>>(new Set());

/** autosize：内容变化时高度自适应；CSS max-h-40 封顶后转滚动。
 *  happy-dom 无真实布局（scrollHeight=0），单测中此函数为无害空转。 */
function autosize() {
  const el = inputEl.value?.element;
  if (!el) return;
  el.style.height = "auto";
  el.style.height = `${el.scrollHeight}px`;
}

watch(input, () => {
  autosize();
});

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

function toggleTarget(id: string) {
  const next = new Set(selectedTargets.value);
  if (next.has(id)) next.delete(id);
  else next.add(id);
  selectedTargets.value = next;
}

function pickMention(id: string) {
  toggleTarget(id);
  input.value = "";
}

// 解析前导 @<name> token（一个或多个，可连写如 @B@C 或以空格/结尾分隔），追加进 targets 并从 content 剥离。
// 语义：前导 @ 与 chip 选择合并去重；无前导 @ 时 content=raw；有前导 @ 时 content=剥离后剩余。
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

function handleSubmit() {
  const raw = input.value.trim();
  if (!raw) return;
  const parsed = parseLeadingMentions(raw);
  const targets = new Set(selectedTargets.value);
  for (const t of parsed.targets) targets.add(t);
  // 有前导 @ 被解析时 content 取剥离后剩余；否则 content 取原文
  const content = parsed.targets.length > 0 ? parsed.content : raw;
  if (!content) return;
  // 空 targets = 整室广播（允许发送）
  emit("send", [...targets], content);
  input.value = "";
  selectedTargets.value = new Set();
}

const placeholder = computed(() =>
  selectedTargets.value.size > 0
    ? t("chat.input.toTarget", {
        name: [...selectedTargets.value]
          .map((id) => memberAgents.value.find((agent) => agent.id === id)?.name ?? id)
          .join(" "),
      })
    : t("chat.input.broadcast"),
);
</script>

<template>
  <form
    class="flex flex-col gap-2 p-2 border-t border-gray-200 dark:border-gray-700 bg-white dark:bg-gray-900"
    @submit.prevent="handleSubmit"
  >
    <!-- room 成员多选 target chips（空选 = 广播） -->
    <div v-if="memberAgents.length > 0" class="flex flex-wrap gap-2">
      <button
        v-for="agent in memberAgents"
        :key="agent.id"
        type="button"
        :class="[
          'px-2 py-0.5 rounded-full text-xs border transition-colors',
          selectedTargets.has(agent.id)
            ? 'bg-blue-100 dark:bg-blue-900 border-blue-400 text-blue-900 dark:text-blue-100'
            : 'bg-white dark:bg-gray-800 border-gray-300 dark:border-gray-600 text-gray-600 dark:text-gray-300 hover:border-blue-400',
        ]"
        @click="toggleTarget(agent.id)"
      >
        @{{ agent.name }}
      </button>
    </div>

    <div class="relative flex gap-2">
      <UiTextField
        ref="inputEl"
        v-model="input"
        multiline
        rows="1"
        :disabled="props.disabled"
        class="flex-1 dark:text-gray-100 dark:placeholder-gray-500 disabled:opacity-50 disabled:cursor-not-allowed resize-none overflow-y-auto max-h-40"
        :placeholder="placeholder"
        @keydown="onKeydown"
      />
      <!-- @-补全下拉 -->
      <ul
        v-if="mentionCandidates.length > 0"
        class="absolute bottom-full mb-1 left-0 right-0 max-h-40 overflow-y-auto bg-white dark:bg-gray-800 border border-gray-300 dark:border-gray-600 rounded shadow-lg z-10"
      >
        <li
          v-for="agent in mentionCandidates"
          :key="agent.id"
          class="px-3 py-1.5 text-sm cursor-pointer hover:bg-blue-50 dark:hover:bg-blue-900 text-gray-700 dark:text-gray-200"
          @mousedown.prevent="pickMention(agent.id)"
        >
          @{{ agent.name }}
        </li>
      </ul>
      <UiButton type="submit" :disabled="props.disabled || !input.trim()" variant="primary">
        {{ t("chat.input.send") }}
      </UiButton>
    </div>

    <div class="text-xs text-gray-400 dark:text-gray-500">{{ t("chat.input.hint") }}</div>
  </form>
</template>
