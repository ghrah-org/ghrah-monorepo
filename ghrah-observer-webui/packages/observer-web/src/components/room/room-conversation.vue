<!--
SPDX-FileCopyrightText: 2026 chenxya <chenxya@ghrah.org>

SPDX-License-Identifier: Apache-2.0
-->
<script setup lang="ts">
import {
  isAnchor,
  projectExchanges,
  useAgentsStore,
  useChatStore,
  useHitlStore,
  useRoomsStore,
  useTranscriptStore,
} from "@ghrah/observer-core";
import type { TranscriptEntryView, TurnCardView } from "@ghrah/protocol";
import { computed, reactive } from "vue";
import { useI18n } from "vue-i18n";
import { roomAlign } from "@/composables/useRoomAlign";
import { useObserver } from "@/composables/useObserver";
import AgentEntry from "./agent-entry.vue";
import HumanEntry from "./human-entry.vue";
import InsertNote from "./insert-note.vue";
import QueueNote from "./queue-note.vue";
import SystemEntry from "./system-entry.vue";
import TurnCard from "./turn-card.vue";
import { waitRequestFromPending, type WaitRequest } from "./wait-request.js";

const props = defineProps<{ projectId: string; roomId: string }>();
const { t } = useI18n();
const transcript = useTranscriptStore();
const chat = useChatStore();
const agents = useAgentsStore();
const rooms = useRoomsStore();
const hitl = useHitlStore();
const { loadTurnDetail } = useObserver();

const entries = computed(() => transcript.entriesOf(props));
const turns = computed(() => transcript.turnsOf(props));
const outgoing = computed(() =>
  chat.entries.filter(
    (entry) => entry.projectId === props.projectId && entry.roomId === props.roomId,
  ),
);
const members = computed(() => rooms.rooms.get(props.roomId)?.members ?? []);
const roster = computed(() => agents.agentsForProject(props.projectId));
const agentIds = computed(
  () =>
    new Set(
      members.value
        .filter(({ subject_type }) => subject_type === "agent")
        .flatMap(({ subject, subject_name }) =>
          subject_name ? [subject, subject_name] : [subject],
        ),
    ),
);

function identity(value: string) {
  const member = members.value.find((m) => m.subject === value || m.subject_name === value);
  const agent = roster.value.find(
    (a) => a.agentId === (member?.subject ?? value) || a.agentName === value,
  );
  return {
    id: agent?.agentId ?? member?.subject ?? value,
    name: member?.subject_name || agent?.agentName || value,
  };
}
function targetLabels(ids: readonly string[] | undefined) {
  return ids?.map((id) => identity(id).name) ?? [];
}

const entryById = computed(() => new Map(entries.value.map((entry) => [entry.entry_id, entry])));
const details = reactive(new Map<string, "loading" | "error">());
function detailOf(turn: TurnCardView) {
  return transcript.turnDetail({
    projectId: props.projectId,
    agentId: turn.agent_name,
    turnId: turn.turn_id,
  });
}

async function expand(turn: TurnCardView) {
  const { roomId } = props;
  const id = turn.turn_id;
  if (detailOf(turn) || details.get(id) === "loading") return;
  details.set(id, "loading");
  try {
    await loadTurnDetail(roomId, id);
    if (detailOf(turn)) details.delete(id);
    else details.set(id, "error");
  } catch {
    details.set(id, "error");
  }
}

const waitsByTurn = computed(() => {
  const map = new Map<string, WaitRequest[]>();
  for (const request of hitl.pendingRequests) {
    if (request.projectId !== props.projectId) continue;
    const wait = waitRequestFromPending(request);
    if (!wait) continue;
    const group = map.get(wait.turnId) ?? [];
    group.push(wait);
    map.set(wait.turnId, group);
  }
  return map;
});

const conversation = computed(() => {
  const history = projectExchanges(entries.value, turns.value, outgoing.value, agentIds.value);
  const groups = history.map((exchange) => ({
    exchange,
    entries: [] as TranscriptEntryView[],
    turns: [] as TurnCardView[],
  }));
  const groupByEntry = new Map(
    groups.flatMap((group) => group.exchange.entryIds.map((id) => [id, group] as const)),
  );
  const groupByTurn = new Map(
    groups.flatMap((group) => group.exchange.turnIds.map((id) => [id, group] as const)),
  );
  const openerByTurn = new Map<string, TranscriptEntryView>();
  const repliesByTurn = new Map<string, TranscriptEntryView[]>();
  const insertsByTurn = new Map<string, TranscriptEntryView[]>();
  const turnIds = new Set(turns.value.map(({ turn_id }) => turn_id));
  const withoutOpener: TurnCardView[] = [];
  const ordered = [...turns.value].sort(
    (a, b) =>
      a.opened_at.localeCompare(b.opened_at) ||
      a.turn_index - b.turn_index ||
      a.turn_id.localeCompare(b.turn_id),
  );
  for (const turn of ordered) {
    const group = groupByTurn.get(turn.turn_id);
    if (!group) continue;
    group.turns.push(turn);
    const opener = entryById.value.get(group.exchange.anchorId);
    if (opener) openerByTurn.set(turn.turn_id, opener);
  }
  for (const entry of entries.value) {
    const group = groupByEntry.get(entry.entry_id);
    const id = entry.turn_id;
    if (
      id &&
      turnIds.has(id) &&
      entry.author_type === "agent" &&
      !isAnchor(entry, agentIds.value)
    ) {
      const replies = repliesByTurn.get(id) ?? [];
      replies.push(entry);
      repliesByTurn.set(id, replies);
      if (group && !groupByTurn.has(id)) groupByTurn.set(id, group);
      continue;
    }
    const targets = new Set(
      entry.deliveries.flatMap(({ turn_id, delivery_policy }) =>
        turn_id && delivery_policy === "inject" ? [turn_id] : [],
      ),
    );
    if (entry.insert_before_turn_id) targets.add(entry.insert_before_turn_id);
    if (entry.author_type === "human") {
      for (const target of targets) {
        const inserts = insertsByTurn.get(target) ?? [];
        inserts.push(entry);
        insertsByTurn.set(target, inserts);
      }
    }
    // 投递给多个目标的消息只显示一次。若它也开启了新轮，保留该轮的锚点。
    const insertGroup = group?.turns.length
      ? undefined
      : [...targets].map((id) => groupByTurn.get(id)).find(Boolean);
    (insertGroup ?? group)?.entries.push(entry);
  }
  for (const turn of ordered) {
    if (openerByTurn.has(turn.turn_id)) continue;
    const group = groupByTurn.get(turn.turn_id);
    if (group) group.turns.push(turn);
    else withoutOpener.push(turn);
  }
  const rank = new Map(ordered.map(({ turn_id }, index) => [turn_id, index]));
  for (const group of groups)
    group.turns.sort((a, b) => (rank.get(a.turn_id) ?? 0) - (rank.get(b.turn_id) ?? 0));
  const visible = groups
    .filter(({ entries, turns }) => entries.length || turns.length)
    .map(({ exchange, entries, turns }) => ({ key: exchange.anchorId, entries, turns }));
  if (withoutOpener.length)
    visible.push({ key: "unanchored-turns", entries: [], turns: withoutOpener });
  const byPendingId = new Map(outgoing.value.map((item) => [`pending:${item.clientSeq}`, item]));
  return {
    groups: visible,
    openerByTurn,
    repliesByTurn,
    insertsByTurn,
    pending: history.flatMap(({ anchorId, optimistic }) => {
      const item = optimistic ? byPendingId.get(anchorId) : undefined;
      return item ? [item] : [];
    }),
  };
});
</script>

<template>
  <div class="room-conversation">
    <template v-for="group in conversation.groups" :key="group.key">
      <template v-for="entry in group.entries" :key="entry.entry_id">
        <InsertNote
          v-if="entry.author_type === 'human' && entry.insert_before_turn_id"
          mode="above"
          :entry="entry"
          :project-id="projectId"
          :align="roomAlign"
          :current-author="chat.currentAuthor"
          :targets="targetLabels(entry.targets)"
        />
        <QueueNote
          v-else-if="entry.author_type === 'human' && entry.human_state === 'queued'"
          :entry="entry"
          :project-id="projectId"
          :align="roomAlign"
          :current-author="chat.currentAuthor"
          :targets="targetLabels(entry.targets)"
        />
        <HumanEntry
          v-else-if="entry.author_type === 'human'"
          :entry="entry"
          :project-id="projectId"
          :align="roomAlign"
          :current-author="chat.currentAuthor"
          :targets="targetLabels(entry.targets)"
        />
        <AgentEntry
          v-else-if="entry.author_type === 'agent'"
          :entry="entry"
          :project-id="projectId"
          :agent-id="identity(entry.author).id"
          :agent-name="identity(entry.author).name"
          :anchor="isAnchor(entry, agentIds)"
          :targets="targetLabels(entry.targets)"
        />
        <SystemEntry v-else :entry="entry" :project-id="projectId" />
      </template>
      <TurnCard
        v-for="turn in group.turns"
        :key="turn.turn_id"
        :turn="turn"
        :room-id="roomId"
        :project-id="projectId"
        :agent-display-name="identity(turn.agent_name).name"
        :opener="conversation.openerByTurn.get(turn.turn_id)"
        :entries="conversation.insertsByTurn.get(turn.turn_id)"
        :replies="conversation.repliesByTurn.get(turn.turn_id)"
        :waits="waitsByTurn.get(turn.turn_id)"
        :detail="detailOf(turn)"
        :loading="details.get(turn.turn_id) === 'loading'"
        :error="details.get(turn.turn_id) === 'error' ? 'load' : null"
        @expand="expand"
      />
    </template>
    <HumanEntry
      v-for="item in conversation.pending"
      :key="item.clientSeq"
      :outgoing="item"
      :project-id="projectId"
      :align="roomAlign"
      :current-author="chat.currentAuthor"
      :targets="targetLabels(item.targets)"
    />
    <template v-if="!conversation.groups.length">
      <p v-if="chat.hasPending({ projectId, roomId })" class="room-skeleton" role="status">
        {{ t("room.humanState.pending") }}
      </p>
      <p v-else-if="!conversation.pending.length" class="room-empty">{{ t("chat.empty") }}</p>
    </template>
  </div>
</template>

<style scoped>
.room-conversation {
  display: flex;
  flex-direction: column;
  gap: var(--spacing-2);
  min-width: 0;
}
.room-empty,
.room-skeleton {
  margin: var(--spacing-3) 0;
  color: var(--color-text-muted);
  text-align: center;
}
</style>
