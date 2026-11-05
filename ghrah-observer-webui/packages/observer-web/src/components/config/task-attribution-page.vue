<!--
SPDX-FileCopyrightText: 2026 chenxya <chenxya@ghrah.org>

SPDX-License-Identifier: Apache-2.0
-->

<script setup lang="ts">
import { useProjectsStore, useTasksStore } from "@ghrah/observer-core";
import { computed } from "vue";
import { useI18n } from "vue-i18n";
import EvidenceBadge from "@/components/task/evidence-badge.vue";

const tasks = useTasksStore();
const projects = useProjectsStore();
const { t } = useI18n();

const projectId = computed(() => projects.activeProjectId);
const projectTasks = computed(() =>
  projectId.value != null ? tasks.tasksByProject(projectId.value) : [...tasks.tasks.values()],
);

interface ClaimRow {
  claimId: string;
  taskId: string;
  taskTitle: string;
  state: string;
  claimant: string;
  verdictBy: string | null | undefined;
  checks: Array<{ checker: string; passed: boolean }>;
  evidence: Array<{ evidence_id: string; kind: string; ref: string }>;
}

const claimRows = computed<ClaimRow[]>(() =>
  projectTasks.value.flatMap((task) =>
    tasks.claimsByTask(task.task_id).map((claim) => ({
      claimId: claim.claim_id,
      taskId: task.task_id,
      taskTitle: task.title,
      state: claim.state,
      claimant: claim.claimant_name ?? claim.claimant_id,
      verdictBy: claim.verdict_by,
      checks: claim.checks.map((check) => ({ checker: check.checker, passed: check.passed })),
      evidence: claim.evidence.map((item) => ({
        evidence_id: item.evidence_id,
        kind: item.kind,
        ref: item.ref,
      })),
    })),
  ),
);
</script>

<template>
  <div class="config-page task-attribution-page">
    <p v-if="projectTasks.length === 0" class="task-attribution-empty">
      {{ t("config.tasks.empty") }}
    </p>
    <p v-else-if="claimRows.length === 0" class="task-attribution-empty">
      {{ t("config.tasks.noClaims") }}
    </p>

    <ul v-if="claimRows.length" class="task-attribution-list">
      <li v-for="row in claimRows" :key="row.claimId" class="task-attribution-row">
        <div class="task-attribution-heading">
          <strong>{{ row.taskTitle }}</strong>
          <code>{{ row.taskId }}</code>
          <span class="task-attribution-state" :data-state="row.state">
            {{ t(`config.tasks.state.${row.state}`) }}
          </span>
        </div>
        <div class="task-attribution-claimant">
          {{ t("config.tasks.claimant") }}: {{ row.claimant }}
          <span v-if="row.verdictBy">
            · {{ t("config.tasks.verdictBy") }}: {{ row.verdictBy }}</span
          >
        </div>
        <div v-if="row.checks.length" class="task-attribution-checks">
          <span
            v-for="check in row.checks"
            :key="check.checker"
            class="task-attribution-check"
            :data-passed="check.passed"
          >
            {{ check.checker }}
          </span>
        </div>
        <div v-if="row.evidence.length" class="task-attribution-evidence">
          <EvidenceBadge
            v-for="item in tasks.evidenceForClaim(row.claimId)"
            :key="item.evidence_id"
            :evidence="item"
          />
        </div>
      </li>
    </ul>
  </div>
</template>
