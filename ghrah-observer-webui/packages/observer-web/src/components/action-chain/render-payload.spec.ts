// SPDX-FileCopyrightText: 2026 chenxya <chenxya@ghrah.org>
//
// SPDX-License-Identifier: Apache-2.0

import type { ActionResultItem, ContentBlock } from "@ghrah/protocol";
import { describe, expect, it } from "vitest";
import {
  jsonFallback,
  MAX_CONTENT_LENGTH,
  renderActionResult,
  renderContentBlock,
  renderers,
} from "./render-payload.js";

function textBlock(text: string): ContentBlock {
  return { type: "text", text };
}

function toolCallBlock(name: string, args: Record<string, unknown> = {}): ContentBlock {
  return { type: "tool_call", id: "tc-1", name, arguments: args };
}

function toolResultBlock(content: string, name: string | null = null): ContentBlock {
  return { type: "tool_result", tool_call_id: "tc-1", name, content, success: true };
}

function actionResult(
  abilityName: string,
  data: Record<string, unknown>,
  outcome: "success" | "failure" | "needs_input" | "delegate" = "success",
): ActionResultItem {
  return { ability_name: abilityName, action_result: { outcome, data } };
}

describe("renderContentBlock", () => {
  it("renders text blocks as markdown", () => {
    expect(renderContentBlock(textBlock("# Title"))).toEqual({
      kind: "markdown",
      source: "# Title",
      truncated: false,
    });
  });

  it("renders reasoning blocks as quote", () => {
    const block = renderContentBlock({
      type: "reasoning",
      reasoning: "thinking...",
      incomplete: false,
    });
    expect(block).toEqual({ kind: "quote", source: "thinking...", truncated: false });
  });

  it("renders image/audio/file blocks as media preview", () => {
    const image = renderContentBlock({
      type: "image",
      url: "https://example.com/a.png",
      mime_type: "image/png",
    });
    expect(image).toMatchObject({
      kind: "media-preview",
      mime: "image/png",
      url: "https://example.com/a.png",
    });

    const audio = renderContentBlock({ type: "audio", data: "QkFTRTY0", mime_type: "audio/wav" });
    expect(audio).toMatchObject({ kind: "media-preview", mime: "audio/wav", base64: "QkFTRTY0" });

    const file = renderContentBlock({
      type: "file",
      url: "https://example.com/report.pdf",
      mime_type: "application/pdf",
      filename: "report.pdf",
    });
    expect(file).toMatchObject({
      kind: "media-preview",
      mime: "application/pdf",
      filename: "report.pdf",
    });

    const video = renderContentBlock({
      type: "video",
      attachment_id: "att-v",
      mime_type: "video/mp4",
      filename: "clip.mp4",
      frames: [{ type: "image", base64: "RlJBTUU=", mime_type: "image/jpeg" }],
    });
    expect(video).toMatchObject({
      kind: "media-preview",
      mime: "video/mp4",
      filename: "clip.mp4",
      base64: "RlJBTUU=",
    });
  });

  it("renders tool_call arguments as kv-table with ability title", () => {
    const block = renderContentBlock(
      toolCallBlock("read_file", { file_path: "src/foo.py", offset: 10 }),
    );
    expect(block).toEqual({
      kind: "kv-table",
      title: "read_file",
      rows: [
        { key: "File Path", value: "src/foo.py", structured: false, truncated: false },
        { key: "Offset", value: "10", structured: false, truncated: false },
      ],
    });
  });

  it("stringifies object/array tool_call values as structured rows", () => {
    const block = renderContentBlock(
      toolCallBlock("execute_command", { files: ["a", "b"], opts: { verbose: true } }),
    );
    expect(block?.kind).toBe("kv-table");
    const rows = (block as { rows: Array<{ key: string; structured: boolean }> }).rows;
    expect(rows.find((r) => r.key === "Files")).toMatchObject({ structured: true });
    expect(rows.find((r) => r.key === "Opts")).toMatchObject({ structured: true });
  });

  it("renders tool_result read_file as file-content (unwrap JSON string)", () => {
    const block = renderContentBlock(toolResultBlock(JSON.stringify("line1\nline2"), "read_file"));
    expect(block).toEqual({ kind: "file-content", content: "line1\nline2", truncated: false });
  });
  it("renders tool_result list_directory as table", () => {
    const content = JSON.stringify([
      { name: "foo.py", type: "file", size: 123 },
      { name: "bar/", type: "dir" },
    ]);
    const block = renderContentBlock(toolResultBlock(content, "list_directory"));
    expect(block).toEqual({
      kind: "table",
      columns: ["name", "type", "size"],
      rows: [
        ["foo.py", "file", "123"],
        ["bar/", "dir", ""],
      ],
      truncated: false,
    });
  });

  it("renders tool_result execute_command as terminal", () => {
    const block = renderContentBlock(toolResultBlock("$ ls\nfile1", "execute_command"));
    expect(block).toEqual({
      kind: "terminal",
      output: "$ ls\nfile1",
      success: true,
      truncated: false,
    });
    const failed = renderContentBlock({
      type: "tool_result",
      tool_call_id: "tc-1",
      name: "execute_command",
      content: "boom",
      success: false,
      error: "exit 1",
    });
    expect(failed).toMatchObject({ kind: "terminal", success: false });
  });

  it("falls back unknown tool_result names to collapsed-json", () => {
    const block = renderContentBlock(toolResultBlock("plain text output", "totally_new_tool"));
    expect(block).toEqual({ kind: "collapsed-json", data: "plain text output", truncated: false });
  });

  it("renders error blocks as error-card", () => {
    const block = renderContentBlock({
      type: "error",
      error_type: "ToolError",
      message: "something broke",
      details: { code: 500 },
    });
    expect(block).toEqual({
      kind: "error-card",
      errorType: "ToolError",
      message: "something broke",
      details: { code: 500 },
    });
  });

  // ── 抑制规则（J4）：chat 视图已展示的内容不在详情区重复 ──

  it("suppresses conversation text blocks only in conversation context", () => {
    expect(renderContentBlock(textBlock("hi"), { abilityNames: ["conversation"] })).toBeNull();
    // 非 conversation 节点的 text block 正常渲染
    expect(renderContentBlock(textBlock("hi"), { abilityNames: ["write_file"] })).toMatchObject({
      kind: "markdown",
      source: "hi",
    });
    expect(renderContentBlock(textBlock("hi"))).toMatchObject({ kind: "markdown" });
  });

  it("suppresses send_message/broadcast tool_calls", () => {
    expect(renderContentBlock(toolCallBlock("send_message"))).toBeNull();
    expect(renderContentBlock(toolCallBlock("broadcast_message"))).toBeNull();
    expect(renderContentBlock(toolCallBlock("execute_command"))).not.toBeNull();
  });

  // ── 截断策略：超长内容截断并标记 ──

  it("truncates oversized content with a flag", () => {
    const huge = "x".repeat(MAX_CONTENT_LENGTH + 100);
    const markdown = renderContentBlock(textBlock(huge));
    expect(markdown).toMatchObject({ kind: "markdown", truncated: true });
    expect((markdown as { source: string }).source.length).toBe(MAX_CONTENT_LENGTH);

    const terminal = renderContentBlock(toolResultBlock(huge, "execute_command"));
    expect(terminal).toMatchObject({ kind: "terminal", truncated: true });

    const fallback = renderContentBlock(toolResultBlock(huge, "unknown_tool"));
    expect(fallback).toMatchObject({ kind: "collapsed-json", truncated: true });
  });
});

describe("renderActionResult", () => {
  it("renders write_file with file_path kv-table titled by the path", () => {
    const block = renderActionResult(
      actionResult("write_file", { file_path: "src/foo.py", bytes: 1234 }),
    );
    expect(block).toEqual({
      kind: "kv-table",
      title: "src/foo.py",
      rows: [
        { key: "File Path", value: "src/foo.py", structured: false, truncated: false },
        { key: "Bytes", value: "1234", structured: false, truncated: false },
      ],
    });
  });

  it("renders edit_file/apply_patch through the write renderer", () => {
    expect(renderActionResult(actionResult("edit_file", { file_path: "a.py" }))).toMatchObject({
      kind: "kv-table",
      title: "a.py",
    });
    expect(renderActionResult(actionResult("apply_patch", { file_path: "b.py" }))).toMatchObject({
      kind: "kv-table",
      title: "b.py",
    });
  });

  it("renders conversation text as markdown, falling back to kv-table", () => {
    const withText = renderActionResult(actionResult("conversation", { text: "hello **world**" }));
    expect(withText).toEqual({ kind: "markdown", source: "hello **world**", truncated: false });

    const withoutText = renderActionResult(actionResult("conversation", { turns: 3 }));
    expect(withoutText).toMatchObject({ kind: "kv-table", title: "conversation" });
    expect(withoutText).not.toBeNull();
  });

  it("renders unknown abilities with generic kv-table on data", () => {
    const block = renderActionResult(actionResult("totally_new", { answer: 42 }));
    expect(block).toMatchObject({
      kind: "kv-table",
      rows: [{ key: "Answer", value: "42", structured: false, truncated: false }],
    });
  });

  it("renders empty-data results with outcome/hint metadata, never blank", () => {
    const block = renderActionResult({
      ability_name: "mystery",
      action_result: { outcome: "failure", data: {}, next_action_hint: "retry" },
    });
    expect(block).toMatchObject({
      kind: "kv-table",
      title: "mystery",
      rows: [
        { key: "Outcome", value: "failure", structured: false, truncated: false },
        { key: "Next Action Hint", value: "retry", structured: false, truncated: false },
      ],
    });

    const empty = renderActionResult({ ability_name: "mystery", action_result: null });
    expect(empty).toMatchObject({ kind: "collapsed-json" });
  });

  it("never returns blank for any action result shape", () => {
    const cases: ActionResultItem[] = [
      actionResult("write_file", {}),
      actionResult("conversation", {}),
      actionResult("query_tasks", {}),
      { ability_name: "x", action_result: undefined },
    ];
    for (const item of cases) {
      const block = renderActionResult(item);
      expect(block).not.toBeNull();
      expect(block).toBeDefined();
    }
  });
});

describe("renderer registry", () => {
  it("exposes ability-name lookup with fallback semantics", () => {
    expect(renderers.write_file).toBeDefined();
    expect(renderers.edit_file).toBeDefined();
    expect(renderers.apply_patch).toBeDefined();
    expect(renderers.conversation).toBeDefined();
    expect(renderers.totally_new_ability).toBeUndefined();
  });
});

describe("jsonFallback", () => {
  it("serializes any unknown structure as collapsed-json", () => {
    const block = jsonFallback({ nested: { a: 1 } });
    expect(block).toEqual({
      kind: "collapsed-json",
      data: '{\n  "nested": {\n    "a": 1\n  }\n}',
      truncated: false,
    });
  });

  it("handles circular structures without throwing", () => {
    const circular: Record<string, unknown> = {};
    circular.self = circular;
    const block = jsonFallback(circular);
    expect(block.kind).toBe("collapsed-json");
    expect((block as { truncated?: boolean }).truncated).toBe(false);
  });
});
