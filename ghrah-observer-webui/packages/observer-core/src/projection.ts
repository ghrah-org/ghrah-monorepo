// SPDX-FileCopyrightText: 2026 chenxya <chenxya@ghrah.org>
//
// SPDX-License-Identifier: Apache-2.0

import type { ActionNode, ContentBlock, RoomLogEntryPayload } from "@ghrah/protocol";
import { type ChatBlock, parseChatBlock, textFromChatBlocks } from "./block-compat.js";

export type ChatEntryKind =
  "human_input" | "conversation" | "send_message" | "broadcast" | "end_task";

export interface ChatEntry {
  deliveries?: Array<{
    delivery_id: string;
    recipient_id: string;
    state: string;
    error?: string | null;
    blocked_reason?: string | null;
  }>;
  projectId?: string;
  agentId?: string;
  sessionId?: string;
  branchId?: string;
  from: string;
  to: string;
  content: string;
  blocks?: ChatBlock[];
  kind: ChatEntryKind;
  timestamp: string;
  nodeId: string;
  agentName: string;
  childSeq: number;
  roomId?: string;
  /** 定向发信目标（data.targets 约定）；缺省/空 = 整室广播。 */
  targets?: string[];
  pending?: boolean;
  error?: string;
}

export interface FileChange {
  projectId: string;
  agentId: string;
  agentName: string;
  sessionId: string;
  branchId: string;
  abilityName: string;
  filePath?: string;
  success: boolean;
  result?: unknown;
  error?: string;
  outcome?: string;
  timestamp: string;
  nodeId: string;
}

export interface FileChangeScope {
  projectId: string;
  agentId: string;
  sessionId?: string;
  branchId?: string;
}

export interface RoomLogProjectionScope {
  projectId: string;
  agentId?: string;
}

const FILE_CHANGE_ABILITIES = new Set(["write_file", "edit_file", "delete_file"]);

/** 从内容块里取可见正文（仅 text 块），供 content 回退与 pending 匹配。 */
export function textFromBlocks(blocks: ChatBlock[] | undefined): string {
  return textFromChatBlocks(blocks);
}

/**
 * 解析 RoomLog data.content_blocks。坏块逐个跳过，不把整条消息判坏；
 * 空数组/缺省/非数组一律视为「没有块」，由调用方回退正文。
 */
export function contentBlocksFromData(data: Record<string, unknown>): ChatBlock[] | undefined {
  const raw = data.content_blocks;
  if (!Array.isArray(raw) || raw.length === 0) return undefined;
  const blocks: ChatBlock[] = [];
  for (const item of raw) {
    const parsed = parseChatBlock(item);
    if (parsed) blocks.push(parsed);
  }
  return blocks.length > 0 ? blocks : undefined;
}

/**
 * 正文与块并存时，把正文补成前置 text 块（块里已有 text 时不重复）。
 * 这样聊天富块模板只渲染 blocks 也不会丢正文；纯块消息保持无 text 块。
 */
export function withTextContent(
  blocks: ChatBlock[] | undefined,
  content: string,
): ChatBlock[] | undefined {
  if (!blocks || blocks.length === 0) return blocks;
  if (!content || blocks.some((block) => block.type === "text")) return blocks;
  return [{ type: "text", text: content }, ...blocks];
}

function _blockArg(block: ContentBlock, key: string): string {
  if (block.type !== "tool_call") return "";
  const v = block.arguments?.[key];
  return typeof v === "string" ? v : "";
}

/**
 * RoomLog data.ability → ChatEntryKind。仅作者为 agent 的条目需要区分：
 * ``end_task``（任务终止的最终答复）单独成类以便渲染成结束态；其余 agent
 * 条目（含链上 conversation 回复 / send 消息）沿用 conversation。
 */
function _roomEntryKind(entry: RoomLogEntryPayload, ability: string): ChatEntryKind {
  if (entry.author_type === "human") return "human_input";
  if (ability === "end_task") return "end_task";
  return "conversation";
}

/**
 * RoomLog entry → ChatEntry。chat 的按 Room 投影（数据源 = rooms store 的 logs）。
 * data 约定（map 信封，协议层不约束）：`{message: string, targets?: string[],
 * ability?: string, content_blocks?: unknown[]}`——message 为正文；content_blocks
 * 来自 MM metadata 回读，解析后填入 `entry.blocks`。纯块消息（message 缺省/空）
 * 不再把 data 的 JSON 兜底当成正文；正文与块并存时正文补成前置 text 块，
 * 避免只在富块模板里渲染块而丢正文。
 * targets 非空 = 定向这些 agent，缺省/空 = 整室广播；ability 为产生该条目的
 * 能力名（RoomFilter 链过滤落账时携带），用于把 ``end_task`` 结果投影成
 * 结束态条目而不是普通会话回复。
 */
export function roomLogToChatEntries(
  entry: RoomLogEntryPayload,
  scope: RoomLogProjectionScope = { projectId: "" },
): ChatEntry {
  const data = entry.data ?? {};
  const message = data.message;
  const rawBlocks = contentBlocksFromData(data);
  const hasBlocks = rawBlocks !== undefined && rawBlocks.length > 0;
  const messageText = typeof message === "string" ? message : "";
  // 纯块消息：正文只回退到块内文本（通常为空），绝不回退 JSON.stringify(data)。
  const content = hasBlocks
    ? messageText || textFromBlocks(rawBlocks)
    : typeof message === "string"
      ? message
      : JSON.stringify(data);
  const blocks = withTextContent(rawBlocks, content);
  const rawTargets = data.targets;
  const targets =
    Array.isArray(rawTargets) && rawTargets.every((t) => typeof t === "string")
      ? (rawTargets as string[])
      : undefined;
  const deliveries = Array.isArray(data.deliveries)
    ? data.deliveries.filter(
        (d): d is NonNullable<ChatEntry["deliveries"]>[number] =>
          !!d &&
          typeof d === "object" &&
          typeof d.delivery_id === "string" &&
          typeof d.recipient_id === "string" &&
          typeof d.state === "string" &&
          (d.error == null || typeof d.error === "string") &&
          (d.blocked_reason == null || typeof d.blocked_reason === "string"),
      )
    : undefined;
  const ability = typeof data.ability === "string" ? data.ability : "";
  return {
    deliveries,
    projectId: scope.projectId,
    agentId: entry.author_type === "agent" ? scope.agentId : undefined,
    from: entry.author,
    to: entry.room_id,
    content,
    blocks,
    kind: _roomEntryKind(entry, ability),
    timestamp: new Date(entry.timestamp * 1000).toISOString(),
    nodeId: entry.id,
    agentName: entry.author_type === "agent" ? entry.author : "",
    childSeq: entry.seq,
    roomId: entry.room_id,
    targets: targets && targets.length > 0 ? targets : undefined,
  };
}

/**
 * @deprecated chat 已改为按 Room 投影（roomLogToChatEntries）；本函数仅保留给
 * ActionChain 面板等 chain 维度消费，不要再用于 chat 流。
 */
export function projectNodeToChatEntries(node: ActionNode): ChatEntry[] {
  const entries: ChatEntry[] = [];
  const agentName = node.agent_name ?? "";
  const timestamp = node.timestamp ?? "";
  const nodeId = node.id ?? "";
  const abilityNames = node.ability_names ?? [];
  const msgs = node.messages_delta ?? [];
  const ars = node.action_results ?? [];

  let childSeq = 0;
  const push = (e: Omit<ChatEntry, "childSeq">): void => {
    entries.push({ ...e, childSeq });
    childSeq += 1;
  };

  for (const m of msgs) {
    const role = m.role;
    const source = m.source ?? "";
    const blocks = m.content_blocks ?? [];

    if (role === "user") {
      if (source.startsWith("human:") || source === "human") {
        const text = textFromBlocks(blocks);
        if (text) {
          push({
            from: source.startsWith("human:") ? source.slice("human:".length) || "user" : "user",
            to: agentName,
            content: text,
            kind: "human_input",
            timestamp,
            nodeId,
            agentName,
          });
        }
      } else if (source.startsWith("agent:")) {
        // R3: inter-agent echo (receiver side) → skip (projected by sender R2)
      }
    } else if (role === "ai") {
      // R2/R6: tool_call 块（send_message / broadcast_message）
      for (const block of blocks) {
        if (block.type !== "tool_call") continue;
        const name = block.name ?? "";
        if (name === "send_message") {
          push({
            from: agentName,
            to: _blockArg(block, "target"),
            content: _blockArg(block, "content"),
            kind: "send_message",
            timestamp,
            nodeId,
            agentName,
          });
        } else if (name === "broadcast_message") {
          push({
            from: agentName,
            to: "all",
            content: _blockArg(block, "content"),
            kind: "broadcast",
            timestamp,
            nodeId,
            agentName,
          });
        }
      }
      // R4: conversation reply (AI TextBlock when ability conversation)
      if (abilityNames.includes("conversation")) {
        const text = textFromBlocks(blocks);
        if (text) {
          push({
            from: agentName,
            to: "user",
            content: text,
            blocks,
            kind: "conversation",
            timestamp,
            nodeId,
            agentName,
          });
        }
      }
    }
  }

  // R5: end_task (from action_results, uniform across auto/toolcall modes)
  if (abilityNames.includes("end_task")) {
    for (const ar of ars) {
      if (ar.ability_name !== "end_task") continue;
      const data = ar.action_result?.data;
      const response = typeof data?.response === "string" ? data.response : "";
      if (response) {
        push({
          from: agentName,
          to: "user",
          content: response,
          kind: "end_task",
          timestamp,
          nodeId,
          agentName,
        });
      }
    }
  }

  // R7: non-comm abilities → no ChatEntry (only in ActionChain view)
  return entries;
}

/**
 * @deprecated chat 已改为按 Room 投影；chain → chat 重建已废弃（chain 投影仅用于
 * ActionChain 面板）。保留仅为兼容，勿用于 chat 流。
 */
export function rebuildChatEntriesFromChain(nodes: ActionNode[]): ChatEntry[] {
  const entries: ChatEntry[] = [];
  for (const node of nodes) {
    entries.push(...projectNodeToChatEntries(node));
  }
  return entries;
}

export function projectNodeToFileChanges(
  node: ActionNode,
  scope: FileChangeScope = { projectId: "", agentId: "", sessionId: "", branchId: "" },
): FileChange[] {
  const out: FileChange[] = [];
  const agentName = node.agent_name ?? "";
  const timestamp = node.timestamp ?? "";
  const nodeId = node.id ?? "";
  const ars = node.action_results ?? [];
  for (const ar of ars) {
    const abilityName = ar.ability_name;
    if (!FILE_CHANGE_ABILITIES.has(abilityName)) continue;
    const actionResult = ar.action_result;
    if (!actionResult) continue;
    const data = (actionResult.data ?? {}) as Record<string, unknown>;
    const filePath = typeof data.file_path === "string" ? data.file_path : undefined;
    const outcome = actionResult.outcome;
    out.push({
      projectId: scope.projectId,
      agentId: scope.agentId,
      agentName,
      sessionId: scope.sessionId ?? node.session_id ?? "",
      branchId: scope.branchId ?? node.created_on_branch_id ?? "",
      abilityName,
      filePath,
      success: outcome === "success",
      result: data,
      error: typeof data.error === "string" ? data.error : undefined,
      outcome,
      timestamp,
      nodeId,
    });
  }
  return out;
}
