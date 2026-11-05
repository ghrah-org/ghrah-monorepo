// SPDX-FileCopyrightText: 2026 chenxya <chenxya@ghrah.org>
//
// SPDX-License-Identifier: Apache-2.0

/** Project-scoped Agent 稳定复合键。name 不参与身份计算。 */
export type AgentKey = `${string}:${string}`;

export function makeAgentKey(projectId: string, agentId: string): AgentKey {
  return `${projectId}:${agentId}`;
}
