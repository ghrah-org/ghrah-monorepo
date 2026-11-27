// SPDX-FileCopyrightText: 2026 chenxya <chenxya@ghrah.org>
//
// SPDX-License-Identifier: Apache-2.0

import type { ActionNode } from "@ghrah/protocol";
import { ActionNodeSchema } from "@ghrah/protocol";
import { describe, expect, it } from "vitest";
import { contentBlocksFromData, projectNodeToFileChanges, withTextContent } from "./projection.js";

function makeNode(overrides: Partial<ActionNode> = {}): ActionNode {
  return ActionNodeSchema.parse(overrides);
}

function actionResults(
  items: {
    ability_name: string;
    outcome?: "success" | "failure" | "needs_input" | "delegate";
    data?: Record<string, unknown>;
  }[],
) {
  return items.map((it) => ({
    ability_name: it.ability_name,
    action_result: { outcome: it.outcome, data: it.data ?? {} },
  }));
}

describe("Room rich content", () => {
  const image = { type: "image", base64: "aGk=", mime_type: "image/png" } as const;

  it("skips invalid blocks and preserves media alongside text", () => {
    const blocks = contentBlocksFromData({ content_blocks: [{ type: "invalid" }, image] });
    expect(blocks).toEqual([image]);
    expect(withTextContent(blocks, "caption")).toEqual([{ type: "text", text: "caption" }, image]);
    expect(withTextContent(blocks, "")).toEqual([image]);
  });
});

describe("projectNodeToFileChanges", () => {
  function fcNode(
    items: {
      ability_name: string;
      outcome?: "success" | "failure" | "needs_input" | "delegate";
      data?: Record<string, unknown>;
    }[],
  ) {
    return makeNode({
      id: "n1",
      agent_name: "agent-1",
      timestamp: "2026-07-17T00:00:00Z",
      action_results: actionResults(items),
    });
  }

  it("write_file change with filePath", () => {
    const fc = projectNodeToFileChanges(
      fcNode([
        { ability_name: "write_file", outcome: "success", data: { file_path: "/tmp/a.txt" } },
      ]),
      { projectId: "proj-001", agentId: "agent-001" },
    );
    expect(fc).toHaveLength(1);
    expect(fc[0]).toMatchObject({
      abilityName: "write_file",
      filePath: "/tmp/a.txt",
      success: true,
      agentName: "agent-1",
      nodeId: "n1",
      timestamp: "2026-07-17T00:00:00Z",
      projectId: "proj-001",
      agentId: "agent-001",
    });
  });

  it("keeps an explicit empty scope for legacy projections", () => {
    const [change] = projectNodeToFileChanges(
      fcNode([{ ability_name: "write_file", outcome: "success", data: {} }]),
    );
    expect(change).toMatchObject({ projectId: "", agentId: "" });
  });

  it("edit_file change", () => {
    expect(
      projectNodeToFileChanges(
        fcNode([
          { ability_name: "edit_file", outcome: "success", data: { file_path: "/tmp/b.ts" } },
        ]),
      )[0].abilityName,
    ).toBe("edit_file");
  });

  it("delete_file change", () => {
    expect(
      projectNodeToFileChanges(
        fcNode([
          { ability_name: "delete_file", outcome: "success", data: { file_path: "/tmp/c" } },
        ]),
      )[0].abilityName,
    ).toBe("delete_file");
  });

  it("filters non-file abilities", () => {
    for (const ab of [
      "file_read",
      "code_exec",
      "conversation",
      "send_message",
      "broadcast_message",
      "end_task",
    ]) {
      expect(
        projectNodeToFileChanges(fcNode([{ ability_name: ab, outcome: "success", data: {} }])),
      ).toEqual([]);
    }
  });

  it("failure outcome with data.error", () => {
    const fc = projectNodeToFileChanges(
      fcNode([
        { ability_name: "write_file", outcome: "failure", data: { error: "Permission denied" } },
      ]),
    );
    expect(fc[0].success).toBe(false);
    expect(fc[0].error).toBe("Permission denied");
    expect(fc[0].outcome).toBe("failure");
  });

  it("missing file_path degrades without throwing", () => {
    const fc = projectNodeToFileChanges(
      fcNode([{ ability_name: "write_file", outcome: "success", data: {} }]),
    );
    expect(fc).toHaveLength(1);
    expect(fc[0].filePath).toBeUndefined();
  });

  it("multiple file abilities preserve order", () => {
    const fc = projectNodeToFileChanges(
      fcNode([
        { ability_name: "write_file", outcome: "success", data: { file_path: "/a" } },
        { ability_name: "edit_file", outcome: "success", data: { file_path: "/b" } },
        { ability_name: "delete_file", outcome: "success", data: { file_path: "/c" } },
      ]),
    );
    expect(fc.map((f) => f.abilityName)).toEqual(["write_file", "edit_file", "delete_file"]);
  });

  it("action_result null skipped without throwing", () => {
    const node = makeNode({
      id: "n1",
      agent_name: "a",
      action_results: [{ ability_name: "write_file", action_result: null }],
    });
    expect(projectNodeToFileChanges(node)).toEqual([]);
  });

  it("test_local_mode_no_ability_result_event: changes still derived from action_results", () => {
    // 反证：仅构造 ActionNode（无 ABILITY_RESULT 概念），changes 从 action_results 产出
    const node = makeNode({
      id: "node-local",
      agent_name: "local-agent",
      ability_names: ["write_file"],
      action_results: actionResults([
        { ability_name: "write_file", outcome: "success", data: { file_path: "/local.txt" } },
      ]),
    });
    const fc = projectNodeToFileChanges(node);
    expect(fc).toHaveLength(1);
    expect(fc[0].filePath).toBe("/local.txt");
  });
});
