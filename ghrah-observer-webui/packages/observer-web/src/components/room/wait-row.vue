<!--
SPDX-FileCopyrightText: 2026 chenxya <chenxya@ghrah.org>

SPDX-License-Identifier: Apache-2.0
-->
<script setup lang="ts">
import { useHitlStore } from "@ghrah/observer-core";
import { computed, nextTick } from "vue";
import { useI18n } from "vue-i18n";
import UiButton from "@/components/ui/button.vue";
import { firstLine } from "./turn-card-model.js";
import type { ReplyType, WaitRequest } from "./wait-request.js";

const props = defineProps<{ request: WaitRequest }>();
const { t } = useI18n();
const hitl = useHitlStore();

function replyHint(reply: ReplyType): string {
  switch (reply.kind) {
    case "boolean":
      return t("room.wait.hint.boolean");
    case "text":
      return t("room.wait.hint.text");
    case "choice":
      if (reply.maxSelections === 1) return t("room.wait.hint.choiceOne");
      return Number.isFinite(reply.maxSelections)
        ? t("room.wait.hint.choiceMany", { n: reply.maxSelections })
        : t("room.wait.hint.choiceUnlimited");
    case "object":
      return t("room.wait.hint.object", { n: reply.fields.length });
  }
}
const hint = computed(() => replyHint(props.request.reply));

const context = computed(() => {
  const { abilityName, toolCall } = props.request.context;
  if (typeof abilityName !== "string" || !abilityName) return "";
  if (!toolCall || typeof toolCall !== "object" || Array.isArray(toolCall)) return abilityName;
  const values: Record<string, unknown> = Object.fromEntries(Object.entries(toolCall));
  const args =
    ["file_path", "command", "path"]
      .map((key) => values[key])
      .find((value): value is string => typeof value === "string" && value.trim().length > 0) ??
    Object.values(values).find(
      (value): value is string => typeof value === "string" && value.trim().length > 0,
    );
  return args
    ? t("room.wait.context.tool", { ability: abilityName, args: firstLine(args) })
    : abilityName;
});

async function jumpToInbox() {
  if (!hitl.pendingRequests.some(({ promiseId }) => promiseId === props.request.requestId)) return;
  hitl.selectedIds.add(props.request.requestId);
  const toggle = document.querySelector<HTMLButtonElement>('[aria-controls="hitl-bar-panel"]');
  if (toggle?.getAttribute("aria-expanded") === "false") toggle.click();
  await nextTick();
  const panel = document.getElementById("hitl-bar-panel");
  panel?.scrollIntoView?.({ block: "nearest" });
  panel?.focus({ preventScroll: true });
}
</script>

<template>
  <div
    class="tc-wait"
    :data-reply-kind="request.reply.kind"
    :data-request-id="request.requestId"
    :data-status="request.status"
  >
    <div>
      <p v-if="request.prompt">{{ request.prompt }}</p>
      <p v-if="context">{{ context }}</p>
      <span class="tc-wait-reply-hint">{{ hint }}</span>
    </div>
    <UiButton
      v-if="request.status === 'pending'"
      class="tc-wait-jump"
      variant="secondary"
      size="xs"
      type="button"
      @click="jumpToInbox"
      >{{ t("room.wait.jump") }}</UiButton
    >
  </div>
</template>

<style scoped>
.tc-wait {
  display: flex;
  flex-wrap: wrap;
  justify-content: space-between;
  align-items: center;
  gap: var(--spacing-2);
  padding: var(--spacing-2);
  border-inline-start: 2px solid var(--color-warn);
  font-size: var(--font-ui-note);
}
.tc-wait p {
  margin: 0;
}
.tc-wait-reply-hint {
  color: var(--color-text-muted);
}
</style>
