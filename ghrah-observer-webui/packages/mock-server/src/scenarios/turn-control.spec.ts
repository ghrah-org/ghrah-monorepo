// SPDX-FileCopyrightText: 2026 chenxya <chenxya@ghrah.org>
//
// SPDX-License-Identifier: Apache-2.0

import { AgentSnapshotSchema, CommandType } from "@ghrah/protocol";
import { afterEach, describe, expect, it, vi } from "vitest";
import { MockState } from "../state.js";
import { getScenario, listScenarios, runScenario } from "./index.js";

afterEach(() => {
  vi.useRealTimers();
});

describe("turn-control scenario", () => {
  it("is registered and starts with open / waiting / closed turns plus an offline agent", () => {
    expect(listScenarios().map(({ name }) => name)).toContain("turn-control");
    const scenario = getScenario("turn-control");
    expect(scenario.name).toBe("turn-control");

    vi.useFakeTimers();
    const state = new MockState();
    const cancel = runScenario(scenario, {
      state,
      apply: (type, payload) => {
        state.handleCommand(type, payload);
      },
    });
    try {
      vi.advanceTimersByTime(16_000);
      const project = [...state.projects.values()].find(({ name }) => name === "demo-project");
      expect(project).toBeDefined();
      const projectId = project!.project_id;
      const turns = state.listTurns(projectId, "demo-architect");
      expect(turns.map(({ status }) => status).sort()).toEqual(["closed", "open", "waiting_hitl"]);

      const offline = project!.agents.find(({ name }) => name === "offline-worker");
      expect(offline).toBeDefined();
      const snapshot = AgentSnapshotSchema.parse(
        state.handleCommand(CommandType.AGENT_ENTITY, {
          project_id: projectId,
          agent_id: offline!.agent_id,
          operation: { type: "snapshot" },
        }).data,
      );
      expect(snapshot.runtime.availability).toBe("offline");

      const active = turns.find(({ status }) => status === "open")!;
      expect(
        state.handleCommand(CommandType.TURN_ABORT, {
          project_id: projectId,
          agent_id: "demo-architect",
          turn_id: active.turn_id,
        }).success,
      ).toBe(true);
      const finished = turns.find(({ status }) => status === "closed")!;
      expect(
        state.handleCommand(CommandType.TURN_ROLLBACK, {
          project_id: projectId,
          agent_id: "demo-architect",
          turn_id: finished.turn_id,
        }).success,
      ).toBe(true);
    } finally {
      cancel();
      state.dispose();
    }
  });
});
