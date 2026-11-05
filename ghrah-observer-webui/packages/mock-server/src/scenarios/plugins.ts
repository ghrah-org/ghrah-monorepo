// SPDX-FileCopyrightText: 2026 chenxya <chenxya@ghrah.org>
//
// SPDX-License-Identifier: Apache-2.0

import { CommandType } from "@ghrah/protocol";
import type { PythonHalfInfo, TaskClaimPayload, TaskInfoPayload } from "@ghrah/protocol";
import type { MockState } from "../state.js";
import type { Scenario } from "./types.js";

/**
 * 插件/归因演示场景：声明 Python 半清单（task-commit-attribution 0.1.0 +
 * 一个版本冲突的 stale-plugin + 一个 python_only 插件）与归因种子
 * （1 个 delivered task + 1 个 verified claim，evidence kind=git_commit）。
 *
 * mock 协商结果是场景声明（非第二权威）：为浏览器 dev 提供可达的 matched 演示；
 * 契约权威在 Python negotiator（S0 测试）+ S1 双侧快照对齐。
 */

const PLUGIN_VERSION = "0.1.0";

const pythonHalves: PythonHalfInfo[] = [
  {
    plugin_id: "task-commit-attribution",
    version: PLUGIN_VERSION,
    provides: ["checker/commit-exists", "evidence-kind/git_commit"],
    requires_capabilities: [],
    instances: [],
  },
  {
    plugin_id: "stale-plugin",
    version: "0.2.0",
    provides: ["checker/stale"],
    requires_capabilities: [],
    instances: [],
  },
  {
    plugin_id: "python-only-plugin",
    version: "1.0.0",
    provides: ["capability:py-only"],
    requires_capabilities: ["webview-sandbox"],
    instances: [],
  },
];

function seedTask(): TaskInfoPayload {
  return {
    task_id: "plugins-task-1",
    project_id: "plugins-project",
    title: "ship attribution demo",
    description: "seeded delivered task with attribution",
    agent_id: "demo-backend",
    agent_name: "demo-backend",
    status: "delivered",
    priority: "normal",
    parent_id: null,
    dependencies: [],
    result: null,
    error: null,
    created_at: "2026-09-21T00:00:00Z",
    updated_at: "2026-09-21T00:01:00Z",
    started_at: "2026-09-21T00:00:10Z",
    completed_at: null,
    metadata: {},
    verification: {
      evidence_min: 1,
      evidence_kinds: ["git_commit"],
      checks: ["commit-exists"],
      approver: null,
    },
  };
}

function seedClaim(): TaskClaimPayload {
  return {
    claim_id: "plugins-claim-1",
    task_id: "plugins-task-1",
    claimant_type: "agent",
    claimant_id: "demo-backend",
    claimant_name: "demo-backend",
    note: "delivered with commit evidence",
    evidence: [
      {
        evidence_id: "plugins-evidence-1",
        kind: "git_commit",
        ref: "ghrah@abc1234",
        digest: "sha256:0000",
        payload: { sha: "abc1234", repo: "ghrah", subject: "feat: attribution demo" },
        created_by: "demo-backend",
        created_at: "2026-09-21T00:00:50Z",
      },
    ],
    state: "submitted",
    checks: [{ checker: "commit-exists", passed: true, detail: null }],
    verdict_by: null,
    verdict_at: null,
    verdict_reason: null,
    provenance: {
      agent_id: "demo-backend",
      session_id: "s-plugins",
      branch_id: "b-plugins",
      node_id: "n-plugins",
    },
    created_at: "2026-09-21T00:00:50Z",
  };
}

const PROJECT_NAME = "plugins-project";

function projectId(state: MockState): string {
  const project = [...state.projects.values()].find((p) => p.name === PROJECT_NAME);
  if (!project) throw new Error(`plugins scenario: project not found: ${PROJECT_NAME}`);
  return project.project_id;
}

export const pluginsScenario: Scenario = {
  name: "plugins",
  description:
    "插件协商/归因演示：task-commit-attribution matched + 版本冲突 + python_only + 归因种子",

  setup(state) {
    state.pythonHalves = pythonHalves;
    // 项目走命令路径创建（与 demo 场景一致）；task/claims/evidence 为归因种子直灌。
    const apply = (commandType: CommandType, payload: Record<string, unknown>) => {
      const outcome = state.handleCommand(commandType, payload);
      if (!outcome.success)
        throw new Error(`plugins scenario: ${commandType} failed: ${outcome.error}`);
    };
    apply(CommandType.PROJECT_CREATE, {
      name: "plugins-project",
      description: "plugin attribution demo project",
    });
    const task = seedTask();
    state.tasks.set(task.task_id, { ...task, project_id: projectId(state) });
    const claim = seedClaim();
    state.claims.set(claim.claim_id, { ...claim, task_id: task.task_id });
    for (const item of claim.evidence) state.evidence.set(item.evidence_id, item);
  },

  timeline: [],
};
