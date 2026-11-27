// SPDX-FileCopyrightText: 2026 chenxya <chenxya@ghrah.org>
//
// SPDX-License-Identifier: Apache-2.0

import { CommandType } from "@ghrah/protocol";
import type { MockState } from "../state.js";
import { demoScenario } from "./demo-4agent-4room.js";
import type { Scenario } from "./types.js";

/**
 * 控制面剧本：在 demo 场景之上补三类轮次与一个离线 agent。
 *
 * 复用 demo 的项目 / room / agent，时间线接在 demo 之后：
 * - 一个 `open` 轮：供中止；
 * - 一个 `waiting_hitl` 轮：供等待行与中止（含 HITL 时的分支）；
 * - 一个已完成轮：供回滚与回滚预览；
 * - 一个 `start=false` 的 agent：供 `agent_offline` 分支。
 *
 * guards / mock-flow 仍跑 demo 场景，本剧本只给 R3 的 e2e 使用。
 */

const PROJECT_NAME = "demo-project";
const ROOM_NAME = "architecture";
/** demo 场景固定 architect 的稳定 agent_id。 */
const CONTROL_AGENT_ID = "demo-architect";
const OFFLINE_AGENT_NAME = "offline-worker";

function projectId(state: MockState): string {
  const project = [...state.projects.values()].find((candidate) => candidate.name === PROJECT_NAME);
  if (!project) throw new Error(`turn-control scenario: project not found: ${PROJECT_NAME}`);
  return project.project_id;
}

function roomId(state: MockState, name: string): string {
  const room = [...state.rooms.values()].find((candidate) => candidate.name === name);
  if (!room) throw new Error(`turn-control scenario: room not found: ${name}`);
  return room.room_id;
}

export const turnControlScenario: Scenario = {
  name: "turn-control",
  description:
    "控制面剧本：open / waiting_hitl / 已完成轮次 + 离线 agent，供中止 / 回滚 / 回滚预览 e2e 共用",

  setup(state) {
    demoScenario.setup?.(state);
  },

  timeline: [
    ...demoScenario.timeline,
    // ── 离线 agent：创建即 stopped，供 turn_abort 的 agent_offline 分支 ──
    [
      15_600,
      ({ state, apply }) =>
        apply(CommandType.AGENT_ENTITY_CREATE, {
          project_id: projectId(state),
          name: OFFLINE_AGENT_NAME,
          template_id: "mock.agent",
          revision: 1,
          start: false,
        }),
    ],
    // ── 三类轮次：open / waiting_hitl / closed ──
    [
      16_000,
      ({ state }) => {
        state.seedTurnControlTurns(roomId(state, ROOM_NAME), CONTROL_AGENT_ID);
      },
    ],
  ],
};
