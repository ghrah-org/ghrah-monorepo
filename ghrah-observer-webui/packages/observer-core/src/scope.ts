// SPDX-FileCopyrightText: 2026 chenxya <chenxya@ghrah.org>
//
// SPDX-License-Identifier: Apache-2.0

/** Project-scoped Agent identity. */
export interface AgentKey {
  projectId: string;
  agentId: string;
}

/** Agent command target. Display name is carried for the current wire contract only. */
export interface AgentTarget extends AgentKey {
  agentName: string;
}

export interface SessionTarget extends AgentTarget {
  sessionId: string;
}

export interface ChainTarget extends SessionTarget {
  branchId: string;
}

export interface RoomTarget {
  projectId: string;
  roomId: string;
}

function tupleKey(parts: string[]): string {
  return JSON.stringify(parts);
}

export function agentKey(target: AgentKey): string {
  return tupleKey([target.projectId, target.agentId]);
}

export function sessionKey(
  target: Pick<SessionTarget, "projectId" | "agentId" | "sessionId">,
): string {
  return tupleKey([target.projectId, target.agentId, target.sessionId]);
}

export function chainKey(target: ChainTarget): string {
  return tupleKey([target.projectId, target.agentId, target.sessionId, target.branchId]);
}

export const branchKey = chainKey;

export function sameAgent(left: AgentKey | null, right: AgentKey): boolean {
  return left?.projectId === right.projectId && left.agentId === right.agentId;
}

export function hasCompleteAgentKey(target: AgentKey): boolean {
  return target.projectId.length > 0 && target.agentId.length > 0;
}

export function hasCompleteChainTarget(target: ChainTarget): boolean {
  return (
    hasCompleteAgentKey(target) &&
    target.agentName.length > 0 &&
    target.sessionId.length > 0 &&
    target.branchId.length > 0
  );
}
