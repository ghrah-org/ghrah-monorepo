import type { ActionNode, ContentBlock, RoomLogEntryPayload } from "@ghrah/protocol";
import { ActionNodeSchema } from "@ghrah/protocol";
import { describe, expect, it } from "vitest";
import {
  projectNodeToChatEntries,
  projectNodeToFileChanges,
  rebuildChatEntriesFromChain,
  roomLogToChatEntries,
} from "./projection.js";

function makeNode(overrides: Partial<ActionNode> = {}): ActionNode {
  return ActionNodeSchema.parse(overrides);
}

function textBlock(t: string): { type: "text"; text: string } {
  return { type: "text", text: t };
}

function toolCall(id: string, name: string, args: Record<string, unknown>) {
  return { type: "tool_call" as const, id, name, arguments: args };
}

function userMsg(blocks: ReturnType<typeof textBlock>[], source: string) {
  return { role: "user" as const, content_blocks: blocks, source, metadata: {} };
}

function aiMsg(blocks: ContentBlock[]) {
  return { role: "ai" as const, content_blocks: blocks, metadata: {} };
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

describe("projectNodeToChatEntries", () => {
  it("R1 human_input from source human:user", () => {
    const node = makeNode({
      id: "n1",
      agent_name: "agent-1",
      timestamp: "2026-07-17T00:00:00Z",
      messages_delta: [userMsg([textBlock("hello there")], "human:user")],
    });
    const entries = projectNodeToChatEntries(node);
    expect(entries).toHaveLength(1);
    expect(entries[0]).toMatchObject({
      from: "user",
      to: "agent-1",
      content: "hello there",
      kind: "human_input",
      nodeId: "n1",
      agentName: "agent-1",
      timestamp: "2026-07-17T00:00:00Z",
      childSeq: 0,
    });
  });

  it("R1 compatible with legacy source 'human'", () => {
    const node = makeNode({
      id: "n1",
      agent_name: "a",
      messages_delta: [userMsg([textBlock("hi")], "human")],
    });
    const entries = projectNodeToChatEntries(node);
    expect(entries[0].from).toBe("user");
    expect(entries[0].kind).toBe("human_input");
  });

  it("R3 inter-agent echo skipped", () => {
    const node = makeNode({
      id: "n1",
      agent_name: "B",
      messages_delta: [userMsg([textBlock("hi")], "agent:A")],
    });
    expect(projectNodeToChatEntries(node)).toEqual([]);
  });

  it("R2 send_message from ai tool_call", () => {
    const node = makeNode({
      id: "n1",
      agent_name: "A",
      messages_delta: [aiMsg([toolCall("c1", "send_message", { target: "B", content: "hey" })])],
    });
    const entries = projectNodeToChatEntries(node);
    expect(entries).toHaveLength(1);
    expect(entries[0]).toMatchObject({
      from: "A",
      to: "B",
      content: "hey",
      kind: "send_message",
      childSeq: 0,
    });
  });

  it("R6 broadcast_message to all", () => {
    const node = makeNode({
      id: "n1",
      agent_name: "A",
      messages_delta: [aiMsg([toolCall("c1", "broadcast_message", { content: "all!" })])],
    });
    const entries = projectNodeToChatEntries(node);
    expect(entries).toHaveLength(1);
    expect(entries[0]).toMatchObject({ from: "A", to: "all", content: "all!", kind: "broadcast" });
  });

  it("R4 conversation reply carries blocks", () => {
    const node = makeNode({
      id: "n1",
      agent_name: "A",
      ability_names: ["conversation"],
      messages_delta: [aiMsg([textBlock("reply text")])],
    });
    const entries = projectNodeToChatEntries(node);
    expect(entries).toHaveLength(1);
    expect(entries[0]).toMatchObject({
      from: "A",
      to: "user",
      content: "reply text",
      kind: "conversation",
    });
    expect(entries[0].blocks).toBeDefined();
  });

  it("R5 end_task auto (empty messages_delta)", () => {
    const node = makeNode({
      id: "n1",
      agent_name: "A",
      ability_names: ["end_task"],
      action_results: actionResults([
        { ability_name: "end_task", outcome: "success", data: { response: "done" } },
      ]),
    });
    const entries = projectNodeToChatEntries(node);
    expect(entries).toHaveLength(1);
    expect(entries[0]).toMatchObject({ kind: "end_task", content: "done", to: "user" });
  });

  it("R5 end_task toolcall unified from action_results", () => {
    const node = makeNode({
      id: "n1",
      agent_name: "A",
      ability_names: ["end_task"],
      messages_delta: [aiMsg([toolCall("c1", "end_task", {})])],
      action_results: actionResults([
        { ability_name: "end_task", outcome: "success", data: { response: "final" } },
      ]),
    });
    const entries = projectNodeToChatEntries(node);
    expect(entries).toHaveLength(1);
    expect(entries[0].content).toBe("final");
  });

  it("R7 non-comm ability produces no entries", () => {
    const node = makeNode({
      id: "n1",
      agent_name: "A",
      ability_names: ["file_read"],
      action_results: actionResults([
        { ability_name: "file_read", outcome: "success", data: { file_path: "/x" } },
      ]),
    });
    expect(projectNodeToChatEntries(node)).toEqual([]);
  });

  it("mixed ability single node yields multiple entries with sequential childSeq", () => {
    const node = makeNode({
      id: "n1",
      agent_name: "A",
      ability_names: ["conversation"],
      messages_delta: [
        aiMsg([toolCall("c1", "send_message", { target: "B", content: "dm" }), textBlock("talk")]),
      ],
    });
    const entries = projectNodeToChatEntries(node);
    expect(entries).toHaveLength(2);
    expect(entries[0].kind).toBe("send_message");
    expect(entries[0].childSeq).toBe(0);
    expect(entries[1].kind).toBe("conversation");
    expect(entries[1].childSeq).toBe(1);
  });
});

describe("rebuildChatEntriesFromChain", () => {
  it("aggregates entries across nodes in order", () => {
    const nodes = [
      makeNode({
        id: "n1",
        agent_name: "A",
        messages_delta: [userMsg([textBlock("hi")], "human:user")],
      }),
      makeNode({
        id: "n2",
        agent_name: "A",
        ability_names: ["conversation"],
        messages_delta: [aiMsg([textBlock("yo")])],
      }),
    ];
    const entries = rebuildChatEntriesFromChain(nodes);
    expect(entries.map((e) => `${e.nodeId}:${e.kind}`)).toEqual([
      "n1:human_input",
      "n2:conversation",
    ]);
  });
});

describe("roomLogToChatEntries", () => {
  function makeRoomEntry(overrides: Partial<RoomLogEntryPayload> = {}): RoomLogEntryPayload {
    return {
      id: "e1",
      room_id: "r1",
      seq: 3,
      author: "agent-1",
      author_type: "agent",
      timestamp: 1750000000,
      data: { message: "hi from room" },
      ...overrides,
    };
  }

  it("agent entry → conversation with structured Project/Agent scope", () => {
    const e = roomLogToChatEntries(makeRoomEntry(), { projectId: "p1", agentId: "a1" });
    expect(e).toMatchObject({
      from: "agent-1",
      to: "r1",
      content: "hi from room",
      kind: "conversation",
      nodeId: "e1",
      childSeq: 3,
      agentName: "agent-1",
      projectId: "p1",
      agentId: "a1",
      roomId: "r1",
    });
    expect(e.timestamp).toBe(new Date(1750000000 * 1000).toISOString());
  });

  it("data.ability=end_task → end_task 条目（不再并入 conversation）", () => {
    const e = roomLogToChatEntries(
      makeRoomEntry({
        data: { message: "全部完成", via: "message_manager", ability: "end_task" },
      }),
      { projectId: "p1", agentId: "a1" },
    );
    expect(e).toMatchObject({
      kind: "end_task",
      content: "全部完成",
      from: "agent-1",
      agentId: "a1",
    });
  });

  it("data.ability 缺省或其他能力 → conversation", () => {
    expect(roomLogToChatEntries(makeRoomEntry()).kind).toBe("conversation");
    expect(
      roomLogToChatEntries(makeRoomEntry({ data: { message: "m", ability: "conversation" } })).kind,
    ).toBe("conversation");
    expect(
      roomLogToChatEntries(makeRoomEntry({ data: { message: "m", ability: "send" } })).kind,
    ).toBe("conversation");
  });

  it("human entry 即使带 ability 也仍是 human_input", () => {
    const e = roomLogToChatEntries(
      makeRoomEntry({
        author: "user",
        author_type: "human",
        data: { message: "m", ability: "end_task" },
      }),
    );
    expect(e.kind).toBe("human_input");
  });

  it("human entry → human_input", () => {
    const e = roomLogToChatEntries(
      makeRoomEntry({ author: "user", author_type: "human", data: { message: "hello" } }),
    );
    expect(e.kind).toBe("human_input");
    expect(e.from).toBe("user");
    expect(e.agentName).toBe("");
  });

  it("data 缺 message 时 content 回退 JSON.stringify(data)", () => {
    const e = roomLogToChatEntries(makeRoomEntry({ data: { foo: 1 } }));
    expect(e.content).toBe(JSON.stringify({ foo: 1 }));
  });

  it("data.targets 为 string[] 时投影到 targets", () => {
    const e = roomLogToChatEntries(
      makeRoomEntry({ data: { message: "hi", targets: ["frontend", "backend"] } }),
    );
    expect(e.targets).toEqual(["frontend", "backend"]);
  });

  it("targets 缺省/空数组/非 string[] 时均为 undefined（广播）", () => {
    expect(roomLogToChatEntries(makeRoomEntry()).targets).toBeUndefined();
    expect(
      roomLogToChatEntries(makeRoomEntry({ data: { message: "m", targets: [] } })).targets,
    ).toBeUndefined();
    expect(
      roomLogToChatEntries(makeRoomEntry({ data: { message: "m", targets: "frontend" } })).targets,
    ).toBeUndefined();
    expect(
      roomLogToChatEntries(makeRoomEntry({ data: { message: "m", targets: [1, 2] } })).targets,
    ).toBeUndefined();
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
