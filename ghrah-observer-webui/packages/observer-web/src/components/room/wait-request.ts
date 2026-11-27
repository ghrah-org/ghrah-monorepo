// SPDX-FileCopyrightText: 2026 chenxya <chenxya@ghrah.org>
//
// SPDX-License-Identifier: Apache-2.0

import type { HitlRequest } from "@ghrah/observer-core";
import type { HitlRequestListView } from "@ghrah/protocol";

export interface ReplyOption {
  value: string;
  label: string;
  description?: string;
}
export type ReplyType =
  | { kind: "boolean"; labels?: { yes: string; no: string } }
  | { kind: "text"; multiline?: boolean; placeholder?: string }
  | { kind: "choice"; options: ReplyOption[]; maxSelections: number }
  | {
      kind: "object";
      fields: { key: string; label: string; type: ReplyType; required?: boolean }[];
    };

export interface WaitRequest {
  requestId: string;
  turnId: string;
  agentId: string;
  prompt: string;
  reply: ReplyType;
  context: Record<string, unknown>;
  deadline: string | null;
  status: "pending" | "responded" | "expired" | "cancelled" | "orphaned";
}

export function waitStatusFromHitl(status: string): WaitRequest["status"] {
  switch (status) {
    case "pending":
    case "expired":
    case "cancelled":
    case "orphaned":
      return status;
    case "approved":
    case "rejected":
    case "responded":
      return "responded";
    default:
      return "orphaned";
  }
}

const approvalReply: ReplyType = {
  kind: "object",
  fields: [
    { key: "approve", label: "approve", type: { kind: "boolean" }, required: true },
    { key: "reason", label: "reason", type: { kind: "text" } },
  ],
};

export function waitRequestFromHitl(
  item: HitlRequestListView["items"][number],
  { agentId }: { agentId: string },
): WaitRequest {
  return {
    requestId: item.promise_id,
    turnId: item.turn_id,
    agentId,
    prompt: item.reason,
    reply: approvalReply,
    context: {
      abilityName: item.ability_name,
      toolCall: item.tool_call,
      shellRemember: item.shell_remember ?? null,
    },
    deadline: item.deadline_at,
    status: waitStatusFromHitl(item.status),
  };
}

export function waitRequestFromPending(request: HitlRequest): WaitRequest | undefined {
  if (!request.turnId) return undefined;
  return {
    requestId: request.promiseId,
    turnId: request.turnId,
    agentId: request.agentId,
    prompt: request.reason,
    reply: approvalReply,
    context: {
      abilityName: request.abilityName,
      toolCall: request.toolArgs,
      shellRemember: request.shellRemember ?? null,
    },
    deadline: request.deadline ?? null,
    status: "pending",
  };
}
