// SPDX-FileCopyrightText: 2026 chenxya <chenxya@ghrah.org>
//
// SPDX-License-Identifier: Apache-2.0

import type { ActionNode } from "@ghrah/protocol";
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
