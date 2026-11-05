// SPDX-FileCopyrightText: 2026 chenxya <chenxya@ghrah.org>
//
// SPDX-License-Identifier: Apache-2.0

import type {
  TaskClaimEventPayload,
  TaskClaimPayload,
  TaskDumpResultPayload,
  TaskEventPayload,
  TaskEvidencePayload,
  TaskInfoPayload,
} from "@ghrah/protocol";
import { EventType } from "@ghrah/protocol";
import { defineStore } from "pinia";
import { ref } from "vue";

/**
 * tasks 投影：legacy 任务事件 + 归因切面（claims/evidence）。
 * - 实时：`TASK_*` 事件 upsert task；`TASK_DELIVERED/VERIFIED/REJECTED`
 *   同时 upsert claim（evidence 内嵌于 claim，wire 原样保留）；
 * - 重建：`task_dump` 读通量全量替换 claims/evidence（tasks 仅 upsert，
 *   不删除 legacy `subject_tasks` 投影——双存储纪律的 Observer 侧对应）。
 */
export const useTasksStore = defineStore("ghrah-tasks", () => {
  const tasks = ref<Map<string, TaskInfoPayload>>(new Map());
  const claims = ref<Map<string, TaskClaimPayload>>(new Map());
  /** 独立 evidence 桶（dump.evidence，表级保真投影）；claim 关联以 claim 内嵌为准。 */
  const evidence = ref<Map<string, TaskEvidencePayload>>(new Map());

  function tasksByProject(projectId: string): TaskInfoPayload[] {
    return [...tasks.value.values()].filter((t) => t.project_id === projectId);
  }

  function claimsByTask(taskId: string): TaskClaimPayload[] {
    return [...claims.value.values()].filter((claim) => claim.task_id === taskId);
  }

  /** claim 的 evidence：claim 内嵌清单（wire 保证给定 tasks 集可完整重建归因链）。 */
  function evidenceForClaim(claimId: string): TaskEvidencePayload[] {
    return claims.value.get(claimId)?.evidence ?? [];
  }

  function upsertTask(task: TaskInfoPayload) {
    const next = new Map(tasks.value);
    next.set(task.task_id, task);
    tasks.value = next;
  }

  function removeTask(taskId: string) {
    const next = new Map(tasks.value);
    next.delete(taskId);
    tasks.value = next;
  }

  /**
   * TaskEventPayload 信封 = { task, previous_status?, reason? }，本身不含删除语义；
   * 由调用方（bind）传入事件类型，TASK_DELETED 移除，其余 upsert。
   */
  function onTaskEvent(eventType: EventType | string, payload: TaskEventPayload) {
    if (eventType === EventType.TASK_DELETED) {
      removeTask(payload.task.task_id);
    } else {
      upsertTask(payload.task);
    }
  }

  /** 归因事件（TASK_DELIVERED/VERIFIED/REJECTED）：upsert task + claim。 */
  function onClaimEvent(_eventType: EventType | string, payload: TaskClaimEventPayload) {
    upsertTask(payload.task);
    upsertClaim(payload.claim);
  }

  /** claim upsert（task_list_claims 回执/归因事件共用；evidence 以内嵌为准）。 */
  function upsertClaim(claim: TaskClaimPayload) {
    const nextClaims = new Map(claims.value);
    nextClaims.set(claim.claim_id, claim);
    claims.value = nextClaims;
  }

  function setTasksFromList(list: TaskInfoPayload[]) {
    tasks.value = new Map(list.map((t) => [t.task_id, t]));
  }

  /** task_dump 读通量重建：claims/evidence 全量替换，tasks 仅 upsert。 */
  function replaceFromDump(dump: TaskDumpResultPayload) {
    for (const task of dump.tasks) upsertTask(task);
    claims.value = new Map(dump.claims.map((claim) => [claim.claim_id, claim]));
    evidence.value = new Map(dump.evidence.map((item) => [item.evidence_id, item]));
  }

  function clearProject(projectId: string) {
    const retainedTasks = new Map(
      [...tasks.value.entries()].filter(([, task]) => task.project_id !== projectId),
    );
    const retainedTaskIds = new Set(retainedTasks.keys());
    const retainedClaims = new Map(
      [...claims.value.entries()].filter(([, claim]) => retainedTaskIds.has(claim.task_id)),
    );
    tasks.value = retainedTasks;
    claims.value = retainedClaims;
  }

  function clearAll() {
    tasks.value = new Map();
    claims.value = new Map();
    evidence.value = new Map();
  }

  return {
    tasks,
    claims,
    evidence,
    tasksByProject,
    claimsByTask,
    evidenceForClaim,
    upsertTask,
    upsertClaim,
    removeTask,
    onTaskEvent,
    onClaimEvent,
    setTasksFromList,
    replaceFromDump,
    clearProject,
    clearAll,
  };
});
