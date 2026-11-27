// SPDX-FileCopyrightText: 2026 chenxya <chenxya@ghrah.org>
//
// SPDX-License-Identifier: Apache-2.0

import { ActionNodeSchema } from "@ghrah/protocol";
import { describe, expect, it } from "vitest";

import { abilityClass, chainToken, nodeClass } from "./ability-class.js";

describe("abilityClass", () => {
  it("write abilities", () => {
    expect(abilityClass(["write_file"])).toBe("write");
    expect(abilityClass(["edit_file"])).toBe("write");
    expect(abilityClass(["apply_patch"])).toBe("write");
    expect(abilityClass(["delete_file"])).toBe("write");
    expect(abilityClass(["move_file"])).toBe("write");
  });

  it("read abilities", () => {
    expect(abilityClass(["read_file"])).toBe("read");
    expect(abilityClass(["list_directory"])).toBe("read");
    expect(abilityClass(["search_code"])).toBe("read");
    expect(abilityClass(["query_tasks"])).toBe("read");
  });

  it("converse abilities", () => {
    expect(abilityClass(["conversation"])).toBe("converse");
    expect(abilityClass(["send_message"])).toBe("converse");
    expect(abilityClass(["broadcast_message"])).toBe("converse");
  });

  it("unknown abilities and empty set", () => {
    expect(abilityClass(["execute_command"])).toBe("unknown");
    expect(abilityClass(["totally_new_ability"])).toBe("unknown");
    expect(abilityClass([])).toBe("unknown");
  });

  it("multiple abilities pick the most prominent (write > read > converse > unknown)", () => {
    expect(abilityClass(["read_file", "write_file"])).toBe("write");
    expect(abilityClass(["conversation", "read_file"])).toBe("read");
    expect(abilityClass(["execute_command", "send_message"])).toBe("converse");
    expect(abilityClass(["execute_command", "totally_new_ability"])).toBe("unknown");
    // 顺序无关
    expect(abilityClass(["write_file", "read_file"])).toBe("write");
    expect(abilityClass(["read_file", "conversation", "write_file"])).toBe("write");
  });

  it("matching is case-insensitive and prefix-based", () => {
    expect(abilityClass(["Write_File"])).toBe("write");
    expect(abilityClass(["READ_FILE"])).toBe("read");
    expect(abilityClass(["search"])).toBe("read");
    expect(abilityClass(["writer_note"])).toBe("unknown"); // writer_ 不是 write_ 前缀
  });
});

describe("nodeClass", () => {
  it.each([
    ["write_file", "write"],
    ["ghrah.fs.read_file", "read"],
    ["send", "converse"],
    ["agent_response", "converse"],
    ["reason_about", "think"],
    ["plan", "think"],
    ["execute_command", "execute"],
    ["shell", "execute"],
    ["mcp_call", "tool"],
    ["tool_search", "tool"],
    ["ghrah.core.spawn_agent", "spawn"],
    ["delegate_task", "spawn"],
    ["custom_member", "unknown"],
  ])("classifies %s as %s", (name, expected) => {
    expect(nodeClass(ActionNodeSchema.parse({ ability_names: [name] }))).toBe(expected);
  });

  it("ranks writing above execution and recognizes compact regardless of ability names", () => {
    const node = ActionNodeSchema.parse({
      ability_names: ["mcp_call", "read_file", "execute_command", "write_file"],
    });
    expect(nodeClass(node)).toBe("write");
    expect(nodeClass({ ...node, metadata: { node_kind: "compact" } })).toBe("compact");
    expect(nodeClass({ ...node, is_snapshot: true })).toBe("compact");
  });

  it("uses the same chain token for running commands and calling tools", () => {
    expect(chainToken("execute")).toBe("var(--chain-tool)");
    expect(chainToken("tool")).toBe("var(--chain-tool)");
    expect(chainToken("compact")).toBe("var(--chain-compact)");
    expect(chainToken("unknown")).toBe("var(--chain-unknown)");
  });
});
