// SPDX-FileCopyrightText: 2026 chenxya <chenxya@ghrah.org>
// SPDX-License-Identifier: Apache-2.0

import { describe, expect, it } from "vitest";

import { RoomSendPayloadSchema } from "./room.js";
import { CommandResultPayloadSchema } from "./system.js";
import {
  TurnAbortPayloadSchema,
  TurnAbortViewSchema,
  TurnRollbackPayloadSchema,
  TurnRollbackPreviewViewSchema,
  TurnRollbackViewSchema,
} from "./turn.js";

const scope = { project_id: "project", agent_id: "agent" };
const rollback = { ...scope, turn_id: "turn" };

describe("Observer 轮次控制契约", () => {
  it("校验公开命令载荷和默认值", () => {
    expect(TurnAbortPayloadSchema.parse(scope)).toEqual({
      ...scope,
      turn_id: null,
      session_id: null,
      reason: "",
    });
    expect(TurnAbortPayloadSchema.safeParse({ ...scope, agent_name: "name" }).success).toBe(false);
    expect(TurnRollbackPayloadSchema.parse(rollback)).toEqual({
      ...rollback,
      target: "base",
      node_id: null,
    });
    expect(
      TurnRollbackPayloadSchema.parse({ ...rollback, target: "node", node_id: "node" }),
    ).toEqual({ ...rollback, target: "node", node_id: "node" });
    for (const fields of [{ target: "node" }, { node_id: "node" }, { dry_run: false }]) {
      expect(TurnRollbackPayloadSchema.safeParse({ ...rollback, ...fields }).success).toBe(false);
    }
  });

  it("解析三个 Python 视图的回执", () => {
    const abort = {
      turn_id: "turn",
      status: "aborted",
      closing_kind: "aborted",
      aborted: false,
      already_terminal: true,
      result_pending: false,
      cancelled_hitl: 0,
      released_deliveries: 0,
      incomplete_node_id: null,
      recovery_branch_id: null,
      opener_delivery_id: null,
    };
    expect(TurnAbortViewSchema.parse(abort)).toEqual(abort);

    const target = {
      turn_id: "turn",
      session_id: "session",
      target: "base",
      target_node_id: "node",
    };
    const rolledBack = {
      ...target,
      status: "rolled_back",
      closing_kind: "completed",
      branch_id: "branch",
      parent_branch_id: null,
      released_delivery_ids: [],
      already_applied: true,
      rolled_back_at: "2026-11-25T00:00:00Z",
    };
    expect(TurnRollbackViewSchema.parse(rolledBack)).toEqual(rolledBack);

    const preview = {
      ...target,
      target_branch_id: "branch",
      base_node_id: "node",
      start_node_id: null,
      end_node_id: "end",
      truncated_node_count: 1,
      crosses_compact: false,
      target_is_compact: false,
      head_unchanged: false,
      released_delivery_ids: [],
    };
    expect(TurnRollbackPreviewViewSchema.parse(preview)).toEqual(preview);
  });

  it("保留回滚错误详情并校验房间投递策略", () => {
    const failed = {
      request_id: "request",
      success: false,
      error: "rollback_target_not_usable",
      details: { suggested_node_ids: ["node"] },
    };
    expect(CommandResultPayloadSchema.parse(failed)).toEqual(failed);
    const room = { room_id: "room", author: "user", author_type: "human" };
    expect(RoomSendPayloadSchema.parse(room).delivery_policy).toBe("queue");
    expect(
      RoomSendPayloadSchema.parse({ ...room, delivery_policy: "inject" }).delivery_policy,
    ).toBe("inject");
    expect(RoomSendPayloadSchema.safeParse({ ...room, delivery_policy: "now" }).success).toBe(
      false,
    );
  });
});
