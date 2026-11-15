// SPDX-FileCopyrightText: 2026 chenxya <chenxya@ghrah.org>
//
// SPDX-License-Identifier: Apache-2.0

/**
 * 尽力渲染：ContentBlock / ActionResultItem → 纯数据渲染描述（阶段 3 任务 3.1）。
 *
 * 输出 PayloadBlock 由组件层按 kind 分支渲染；每条路径都有 JSON 折叠兜底，
 * 绝不返回空白（未知结构 → collapsed-json）。
 */
import type { ActionResultItem, ContentBlock } from "@ghrah/protocol";

/** 序列化/文件内容截断阈值（超出截断并标记，防大 JSON 卡死详情区）。 */
export const MAX_CONTENT_LENGTH = 64 * 1024;

/** 抑制的 tool_call 能力名（chat 视图已展示；归置裁决延续，显式规则 J4）。 */
export const SUPPRESSED_TOOL_CALL_NAMES = new Set(["send_message", "broadcast_message"]);

export interface KvRow {
  key: string;
  value: string;
  /** 值为对象/数组时的结构化展示标记。 */
  structured?: boolean;
  truncated?: boolean;
}

export type PayloadBlock =
  | { kind: "markdown"; source: string; truncated?: boolean }
  | { kind: "quote"; source: string; truncated?: boolean }
  | { kind: "kv-table"; title?: string; rows: KvRow[] }
  | { kind: "file-content"; filename?: string; content: string; truncated?: boolean }
  | { kind: "table"; columns: string[]; rows: string[][]; truncated?: boolean }
  | {
      kind: "terminal";
      command?: string;
      output: string;
      success: boolean;
      truncated?: boolean;
    }
  | {
      kind: "media-preview";
      mime: string;
      url?: string;
      base64?: string;
      filename?: string;
    }
  | { kind: "error-card"; errorType: string; message: string; details?: unknown }
  | { kind: "collapsed-json"; data: unknown; truncated?: boolean };

function clip(text: string): { text: string; truncated: boolean } {
  if (text.length <= MAX_CONTENT_LENGTH) return { text, truncated: false };
  return { text: text.slice(0, MAX_CONTENT_LENGTH), truncated: true };
}

function stringifyValue(value: unknown): { text: string; structured: boolean } {
  if (value == null) return { text: "", structured: false };
  if (typeof value === "string") return { text: value, structured: false };
  if (typeof value === "number" || typeof value === "boolean") {
    return { text: String(value), structured: false };
  }
  return { text: safeJson(value), structured: true };
}

export function safeJson(value: unknown): string {
  try {
    return JSON.stringify(value, null, 2) ?? String(value);
  } catch {
    return String(value);
  }
}

/** 归一化行键：snake_case / camelCase / kebab-case → 空格分词、首字母大写。 */
export function humanizeKey(key: string): string {
  const words = key
    .replace(/([a-z0-9])([A-Z])/g, "$1 $2")
    .replace(/[_\-.]+/g, " ")
    .trim()
    .split(/\s+/)
    .filter(Boolean);
  if (words.length === 0) return key;
  return words
    .map((w) => (w.length <= 3 && w === w.toUpperCase() ? w : w[0].toUpperCase() + w.slice(1)))
    .join(" ");
}

function kvTable(title: string | undefined, data: Record<string, unknown>): PayloadBlock {
  const rows: KvRow[] = [];
  for (const [key, raw] of Object.entries(data)) {
    if (raw == null) continue;
    const { text, structured } = stringifyValue(raw);
    const { text: value, truncated } = structured ? clip(text) : { text, truncated: false };
    rows.push({ key: humanizeKey(key), value, structured, truncated });
  }
  if (rows.length === 0) {
    // 空参数对象也保持可渲染（空表头提示），不返回空白。
    return { kind: "kv-table", title, rows: [] };
  }
  return { kind: "kv-table", title, rows };
}

function extractString(data: Record<string, unknown>, keys: string[]): string | undefined {
  for (const key of keys) {
    const value = data[key];
    if (typeof value === "string" && value.length > 0) return value;
  }
  return undefined;
}

/** file/path 类字段提取口径（与 projectNodeToFileChanges 的 file_path 提取思路对齐）。 */
export const FILE_PATH_KEYS = ["file_path", "filePath", "path", "filename", "file_name"];

// ── tool_result 按能力名分派的渲染器 ──

type ToolResultRenderer = (block: ContentBlock & { type: "tool_result" }) => PayloadBlock;

function renderFileContent(block: ContentBlock & { type: "tool_result" }): PayloadBlock {
  let content = block.content;
  try {
    const parsed: unknown = JSON.parse(content);
    if (typeof parsed === "string") content = parsed;
  } catch {
    // 非 JSON 内容原样展示
  }
  const { text, truncated } = clip(content);
  return { kind: "file-content", content: text, truncated };
}

function renderListDirectory(block: ContentBlock & { type: "tool_result" }): PayloadBlock {
  let entries: unknown;
  try {
    entries = JSON.parse(block.content);
  } catch {
    entries = null;
  }
  if (!Array.isArray(entries)) return jsonFallback(block.content);
  const rows: string[][] = [];
  for (const entry of entries) {
    if (entry != null && typeof entry === "object") {
      const record = entry as Record<string, unknown>;
      rows.push([
        stringifyValue(record.name ?? record.path ?? "").text,
        stringifyValue(record.type ?? record.kind ?? "").text,
        stringifyValue(record.size ?? "").text,
      ]);
    } else {
      rows.push([stringifyValue(entry).text, "", ""]);
    }
  }
  return { kind: "table", columns: ["name", "type", "size"], rows, truncated: false };
}

function renderExecuteCommand(block: ContentBlock & { type: "tool_result" }): PayloadBlock {
  const { text, truncated } = clip(block.content);
  return { kind: "terminal", output: text, success: block.success !== false, truncated };
}

const TOOL_RESULT_RENDERERS: Record<string, ToolResultRenderer> = {
  read_file: renderFileContent,
  list_directory: renderListDirectory,
  execute_command: renderExecuteCommand,
  run_command: renderExecuteCommand,
};

// ── action_results 按 ability_name 分派的渲染器 ──

export type AbilityRenderer = (item: ActionResultItem) => PayloadBlock;

function writeFileRenderer(item: ActionResultItem): PayloadBlock {
  const data = item.action_result?.data ?? {};
  const fileTitle = extractString(data, FILE_PATH_KEYS);
  return kvTable(fileTitle, data);
}

function conversationRenderer(item: ActionResultItem): PayloadBlock {
  // conversation → 文本（设计 §1.4）：data 中的文本字段以 markdown 呈现，无则回退通用键值表。
  const data = item.action_result?.data ?? {};
  const text = extractString(data, ["text", "response", "content", "message"]);
  if (text != null) {
    const { text: clipped, truncated } = clip(text);
    return { kind: "markdown", source: clipped, truncated };
  }
  return kvTable(item.ability_name, data);
}

/** 通用键值表：action_result.data 平铺。 */
function genericAbilityRenderer(item: ActionResultItem): PayloadBlock {
  return kvTable(undefined, item.action_result?.data ?? {});
}

/** JSON 兜底：未知结构折叠展示（尽力渲染的最后一层，绝不空白）。 */
export function jsonFallback(data: unknown): PayloadBlock {
  const text = typeof data === "string" ? data : safeJson(data);
  const { text: clipped, truncated } = clip(text);
  return { kind: "collapsed-json", data: clipped, truncated };
}

/** 渲染器注册表：按 ability_name 查表，未命中走 jsonFallback。 */
export const renderers: Record<string, AbilityRenderer> = {
  write_file: writeFileRenderer,
  edit_file: writeFileRenderer,
  apply_patch: writeFileRenderer,
  conversation: conversationRenderer,
};

/**
 * ContentBlock → 渲染描述；返回 null 表示该 block 被抑制（调用方跳过）。
 *
 * 抑制规则（J4）：conversation 节点的 text block、send_message/broadcast*
 * tool_call 已在 chat 视图展示。
 */
export function renderContentBlock(
  block: ContentBlock,
  context: { abilityNames?: string[] } = {},
): PayloadBlock | null {
  switch (block.type) {
    case "text": {
      if ((context.abilityNames ?? []).includes("conversation")) return null;
      const { text, truncated } = clip(block.text);
      return { kind: "markdown", source: text, truncated };
    }
    case "reasoning": {
      const { text, truncated } = clip(block.reasoning);
      return { kind: "quote", source: text, truncated };
    }
    case "image": {
      const url = block.url ?? undefined;
      const base64 = block.base64 ?? undefined;
      return {
        kind: "media-preview",
        mime: block.mime_type ?? "image/*",
        url,
        base64,
      };
    }
    case "audio": {
      return { kind: "media-preview", mime: block.mime_type, base64: block.data };
    }
    case "file": {
      return {
        kind: "media-preview",
        mime: block.mime_type ?? "application/octet-stream",
        url: block.url ?? undefined,
        base64: block.base64 ?? undefined,
        filename: block.filename ?? undefined,
      };
    }
    case "video": {
      // ActionChain 不解析项目作用域，附件 URL 留空；封面帧可作图片预览。
      return {
        kind: "media-preview",
        mime: block.mime_type ?? "video/*",
        url: block.url ?? undefined,
        base64: block.frames?.[0]?.base64 ?? block.base64 ?? undefined,
        filename: block.filename ?? undefined,
      };
    }
    case "tool_call": {
      if (SUPPRESSED_TOOL_CALL_NAMES.has(block.name)) return null;
      return kvTable(block.name, block.arguments);
    }
    case "tool_result": {
      const renderer =
        block.name != null ? TOOL_RESULT_RENDERERS[block.name.toLowerCase()] : undefined;
      if (renderer) return renderer(block);
      return jsonFallback(block.content);
    }
    case "error": {
      return {
        kind: "error-card",
        errorType: block.error_type,
        message: block.message,
        details: block.details ?? undefined,
      };
    }
    default: {
      // 判别联合已穷尽；防御未来新增类型
      const exhaustive: never = block;
      return jsonFallback(exhaustive);
    }
  }
}

/** ActionResultItem → 渲染描述（按 ability_name 查表，未命中通用键值表）。 */
export function renderActionResult(item: ActionResultItem): PayloadBlock {
  const renderer = renderers[item.ability_name];
  if (renderer) return renderer(item);
  const data = item.action_result?.data ?? {};
  if (Object.keys(data).length === 0) {
    // data 为空时展示 outcome/hint 元信息，避免空表
    const meta: Record<string, unknown> = {};
    if (item.action_result?.outcome) meta.outcome = item.action_result.outcome;
    if (item.action_result?.next_action_hint) {
      meta.next_action_hint = item.action_result.next_action_hint;
    }
    if (Object.keys(meta).length > 0) return kvTable(item.ability_name, meta);
    return jsonFallback(item);
  }
  return genericAbilityRenderer(item);
}
