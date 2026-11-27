// SPDX-FileCopyrightText: 2026 chenxya <chenxya@ghrah.org>
//
// SPDX-License-Identifier: Apache-2.0

import { humanStateOf } from "@ghrah/observer-core";
import type { ActionNode, TranscriptEntryView, TurnCardView } from "@ghrah/protocol";

import { nodeClass, type AbilityClass } from "../action-chain/ability-class.js";

type Translate = (key: string, values: Record<string, number | string>) => string;

export type Phase =
  | { kind: "done" }
  | { kind: "failed" | "aborted" | "rolled_back"; reason: string | null }
  | { kind: "interrupted" }
  | { kind: "waiting_hitl"; since: string }
  | { kind: "acting"; abilityClass: AbilityClass; step: number }
  | { kind: "idle" };

export function nodeError(node: ActionNode | undefined): string | null {
  const error = node?.metadata.error;
  if (typeof error === "string" && error.trim()) return error;
  const reason = node?.metadata.incomplete_reason;
  return typeof reason === "string" && reason.trim() ? reason : null;
}

export function derivePhase(turn: TurnCardView, nodes: readonly ActionNode[], _now: number): Phase {
  switch (turn.status) {
    case "closed":
      return { kind: "done" };
    case "aborted":
    case "rolled_back":
      return { kind: turn.status, reason: turn.abort_reason };
    case "failed":
      return {
        kind: "failed",
        reason:
          turn.abort_reason || nodeError(nodes.find((node) => node.id === turn.incomplete_node_id)),
      };
    case "interrupted":
      return { kind: "interrupted" };
    case "waiting_hitl":
      return { kind: "waiting_hitl", since: turn.waiting_since ?? turn.opened_at };
    case "open": {
      let running: ActionNode | undefined;
      let lastStep: ActionNode | undefined;
      for (const node of nodes) {
        const status = node.metadata.status;
        if (status === "running") running = node;
        else if (status === "completed" || status === "failed") lastStep = node;
      }
      if (running) {
        return {
          kind: "acting",
          abilityClass: nodeClass(lastStep ?? running),
          step: turn.stats.steps,
        };
      }
      return { kind: "idle" };
    }
    default:
      return { kind: "idle" };
  }
}

export function turnStats(turn: TurnCardView, _nodes: readonly ActionNode[], now: number) {
  const { stats } = turn;
  const elapsed = now - Date.parse(turn.opened_at);
  const active = turn.status === "open" || turn.status === "waiting_hitl";
  return {
    steps: stats.steps,
    durationMs: active
      ? Number.isFinite(elapsed)
        ? Math.max(0, elapsed)
        : null
      : stats.duration_ms,
    tokens: {
      input: stats.token_input,
      output: stats.token_output,
      total: stats.token_total,
      cacheRead: stats.token_cache_read,
      cacheWrite: stats.token_cache_write,
    },
    cacheHitRate: stats.cache_hit_rate,
  };
}

export function formatDuration(ms: number | null): string {
  if (ms === null || !Number.isFinite(ms)) return "—";
  const seconds = Math.floor(Math.max(0, ms) / 1000);
  const hours = Math.floor(seconds / 3600);
  const minutes = Math.floor((seconds % 3600) / 60);
  if (hours > 0) return minutes > 0 ? `${hours}h${minutes}m` : `${hours}h`;
  return minutes > 0 ? `${minutes}m${seconds % 60}s` : `${seconds}s`;
}

export function formatTokens(value: number | null): string {
  if (value === null || !Number.isFinite(value)) return "—";
  const absolute = Math.abs(value);
  if (absolute < 1000) return String(value);
  if (absolute < 1000000) return `${Math.round(value / 100) / 10}k`;
  return `${Math.round(value / 100000) / 10}M`;
}

export function formatCacheRate(rate: number | null): string {
  return rate === null || !Number.isFinite(rate) ? "—" : `${Math.round(rate * 100)}%`;
}

export function formatCount(value: number | null): string {
  return value === null || !Number.isFinite(value) ? "—" : String(value);
}

export function agentColorIndex(agentId: string): number {
  let hash = 0x811c9dc5;
  for (const byte of new TextEncoder().encode(agentId)) {
    hash = Math.imul(hash ^ byte, 0x01000193);
  }
  return ((hash >>> 0) % 8) + 1;
}

export function triggerText(
  turn: TurnCardView,
  opener: Pick<TranscriptEntryView, "author" | "author_type"> | null,
  t: Translate,
): string {
  if (!opener?.author || (opener.author_type !== "human" && opener.author_type !== "agent")) {
    return "";
  }
  const name = opener.author_type === "agent" ? `@${opener.author}` : opener.author;
  const trigger = t(`room.card.trigger.${opener.author_type}`, { name });
  return turn.parent_turn_id ? `${trigger} ${t("room.card.trigger.subAgent", {})}` : trigger;
}

function withReason(label: string, reason: string | null | undefined, t: Translate): string {
  return reason ? t("room.phase.reason", { label, reason }) : label;
}

export function phaseText(phase: Phase, t: Translate): string {
  switch (phase.kind) {
    case "failed":
    case "aborted":
      return withReason(t(`room.phase.${phase.kind}`, {}), phase.reason, t);
    case "rolled_back":
      return withReason(t("room.phase.rolledBack", {}), phase.reason, t);
    case "waiting_hitl":
      return t("room.phase.waitingHitl", {});
    case "acting":
      return t("room.phase.acting", {
        ability: t(`room.verb.${phase.abilityClass}`, {}),
        n: phase.step,
      });
    case "idle":
      return "";
    default:
      return t(`room.phase.${phase.kind}`, {});
  }
}

export function humanStateText(entry: TranscriptEntryView, pending: boolean, t: Translate): string {
  const state = humanStateOf(entry, pending);
  if (!state) return "";
  if (state === "failed") {
    return withReason(
      t("room.humanState.failed", {}),
      entry.deliveries.find((d) => d.error)?.error,
      t,
    );
  }
  if (state === "injected" && entry.injection_step !== null) {
    return t("room.humanState.injectedStep", { n: entry.injection_step });
  }
  if (state === "queued") {
    const position = entry.deliveries.find(
      (delivery) => delivery.queue_position > 0,
    )?.queue_position;
    if (position !== undefined) return t("room.humanState.queuedPosition", { n: position });
  }
  return t(`room.humanState.${state}`, {});
}

export function firstLine(value: string): string {
  return value.trim().split(/\r?\n/, 1)[0]?.trim() ?? "";
}

export function nodeSummary(node: ActionNode | undefined): string {
  if (!node) return "";
  for (const message of node.messages_delta) {
    if (message.role !== "ai") continue;
    for (const block of message.content_blocks) {
      if (block.type === "text" && block.text.trim()) return firstLine(block.text);
    }
  }
  return node.ability_names[0] ?? "";
}

export function injectedSummary(entries: readonly TranscriptEntryView[], turnId: string) {
  const inlineInserts: { entry: TranscriptEntryView; step: number }[] = [];
  const waitingInserts: TranscriptEntryView[] = [];
  for (const entry of entries) {
    if (entry.author_type !== "human") continue;
    // 多目标投递的顶层注入字段只代表首个目标。
    const delivery = entry.deliveries.find(
      (item) => item.turn_id === turnId && item.delivery_policy === "inject",
    );
    const legacy =
      !delivery && entry.deliveries.length === 0 && entry.insert_before_turn_id === turnId;
    const step = delivery ? delivery.injection_step : legacy ? entry.injection_step : null;
    if (step !== null) {
      inlineInserts.push({ entry, step });
    } else if (
      delivery?.display_state === "injected" ||
      (legacy && entry.human_state === "injected")
    ) {
      waitingInserts.push(entry);
    }
  }
  return {
    injected: inlineInserts.length,
    waiting: waitingInserts.length,
    inlineInserts,
    waitingInserts,
  };
}

export function formatClock(value: string | number, locale: string): string {
  const date = new Date(value);
  if (!Number.isFinite(date.getTime())) return "";
  return date.toLocaleTimeString(locale, { hour: "2-digit", minute: "2-digit", hourCycle: "h23" });
}
