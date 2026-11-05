// SPDX-FileCopyrightText: 2026 chenxya <chenxya@ghrah.org>
//
// SPDX-License-Identifier: Apache-2.0

import { useAgentsStore } from "@ghrah/observer-core";
import type { RoomMember } from "@ghrah/protocol";

/** 按 subject_name、Project 内稳定 Agent ID、原始 subject 的顺序解析成员显示名。 */
export function useRoomMemberLabel() {
  const agents = useAgentsStore();

  return (member: RoomMember, projectId: string): string => {
    if (member.subject_name) return member.subject_name;
    return (
      agents.agentsForProject(projectId).find((agent) => agent.agentId === member.subject)
        ?.agentName ?? member.subject
    );
  };
}
