// SPDX-FileCopyrightText: 2026 chenxya <chenxya@ghrah.org>
//
// SPDX-License-Identifier: Apache-2.0

// @vitest-environment happy-dom

import type { ActionNode } from "@ghrah/protocol";
import { mount } from "@vue/test-utils";
import { describe, expect, it } from "vitest";
import ActionChainDetail from "./action-chain-detail.vue";

function node(overrides: Partial<ActionNode> = {}): ActionNode {
  return {
    id: "n1",
    parent_id: null,
    agent_name: "alpha",
    timestamp: "2026-09-19T00:00:00Z",
    iteration: 3,
    ability_names: ["write_file"],
    messages_delta: [],
    action_results: [],
    ...overrides,
  } as ActionNode;
}

describe("ActionChainDetail", () => {
  it("renders summary header with ability class and outcome aggregation", () => {
    const wrapper = mount(ActionChainDetail, {
      props: {
        node: node({
          action_results: [
            { ability_name: "conversation", action_result: { outcome: "success", data: {} } },
            { ability_name: "write_file", action_result: { outcome: "failure", data: {} } },
          ],
        }),
      },
    });
    // 任一 failure 即 failure（最显眼聚合）
    expect(wrapper.find(".ac-detail-header--write").exists()).toBe(true);
    expect(wrapper.find(".ac-detail-outcome--failure").exists()).toBe(true);
    expect(wrapper.text()).toContain("iter=3");
    expect(wrapper.text()).toContain("Failure");
  });

  it("aggregates first non-failure outcome when no failure exists", () => {
    const wrapper = mount(ActionChainDetail, {
      props: {
        node: node({
          action_results: [
            { ability_name: "conversation", action_result: { outcome: "needs_input", data: {} } },
          ],
        }),
      },
    });
    expect(wrapper.find(".ac-detail-outcome--needs_input").exists()).toBe(true);
  });

  it("renders write_file result as a kv-table with file path", () => {
    const wrapper = mount(ActionChainDetail, {
      props: {
        node: node({
          action_results: [
            {
              ability_name: "write_file",
              action_result: { outcome: "success", data: { file_path: "src/foo.py", bytes: 12 } },
            },
          ],
        }),
      },
    });
    expect(wrapper.find(".ac-kv-table").exists()).toBe(true);
    expect(wrapper.text()).toContain("src/foo.py");
  });

  it("renders unknown abilities with JSON collapsed fallback", () => {
    const wrapper = mount(ActionChainDetail, {
      props: {
        node: node({
          ability_names: ["mystery"],
          action_results: [{ ability_name: "mystery", action_result: null }],
        }),
      },
    });
    expect(wrapper.find(".ac-json").exists()).toBe(true);
  });

  it("renders messages_delta markdown as HTML via useMarkdown", () => {
    const wrapper = mount(ActionChainDetail, {
      props: {
        node: node({
          messages_delta: [
            {
              role: "ai",
              metadata: {},
              content_blocks: [{ type: "text", text: "# Title\n\n**bold**" }],
            },
          ],
        }),
      },
    });
    const md = wrapper.find(".ac-markdown");
    expect(md.exists()).toBe(true);
    expect(md.element.innerHTML).toContain("<h1>");
    expect(md.element.innerHTML).toContain("<strong>");
  });

  it("renders reasoning blocks as quote style", () => {
    const wrapper = mount(ActionChainDetail, {
      props: {
        node: node({
          messages_delta: [
            {
              role: "ai",
              metadata: {},
              content_blocks: [{ type: "reasoning", reasoning: "thinking...", incomplete: false }],
            },
          ],
        }),
      },
    });
    const quote = wrapper.find("blockquote.ac-quote");
    expect(quote.exists()).toBe(true);
    expect(quote.text()).toContain("thinking...");
  });

  it("suppresses conversation text and send_message in a conversation node", () => {
    const wrapper = mount(ActionChainDetail, {
      props: {
        node: node({
          ability_names: ["conversation"],
          messages_delta: [
            {
              role: "ai",
              metadata: {},
              content_blocks: [
                { type: "text", text: "hello reply" },
                { type: "tool_call", id: "x", name: "send_message", arguments: {} },
              ],
            },
          ],
        }),
      },
    });
    expect(wrapper.text()).not.toContain("hello reply");
    expect(wrapper.text()).not.toContain("send_message");
    // 全部抑制后走空态提示，不渲染任何块
    expect(wrapper.findAll(".ac-block").length).toBe(0);
    expect(wrapper.text()).toContain("No message blocks");
  });

  it("does not suppress plain text in non-conversation nodes", () => {
    const wrapper = mount(ActionChainDetail, {
      props: {
        node: node({
          messages_delta: [
            {
              role: "user",
              metadata: {},
              content_blocks: [{ type: "text", text: "plain user note" }],
            },
          ],
        }),
      },
    });
    expect(wrapper.text()).toContain("plain user note");
  });

  it("renders error blocks as error card", () => {
    const wrapper = mount(ActionChainDetail, {
      props: {
        node: node({
          messages_delta: [
            {
              role: "tool",
              metadata: {},
              content_blocks: [{ type: "error", error_type: "ToolError", message: "boom" }],
            },
          ],
        }),
      },
    });
    expect(wrapper.find(".ac-error-card").exists()).toBe(true);
    expect(wrapper.text()).toContain("ToolError");
  });

  it("shows placeholder when node is null", () => {
    const wrapper = mount(ActionChainDetail, { props: { node: null } });
    expect(wrapper.find(".ac-detail--empty").exists()).toBe(true);
    expect(wrapper.text()).toContain("Select a node");
  });

  it("does not execute scripts embedded in markdown (XSS)", () => {
    const wrapper = mount(ActionChainDetail, {
      props: {
        node: node({
          messages_delta: [
            {
              role: "ai",
              metadata: {},
              content_blocks: [{ type: "text", text: '<script>alert("x")</script>\n\nok' }],
            },
          ],
        }),
      },
    });
    expect(wrapper.find(".ac-markdown script").exists()).toBe(false);
    expect(wrapper.text()).toContain("ok");
  });
});
