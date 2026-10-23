<script setup lang="ts">
import { type AgentTarget, useConnectionStore, useProjectsStore } from "@ghrah/observer-core";
import { computed, onBeforeUnmount, ref } from "vue";
import { useI18n } from "vue-i18n";
import { useObserver } from "@/composables/useObserver";

const props = defineProps<{ projectId: string }>();
const { t } = useI18n();
const projects = useProjectsStore();
const connection = useConnectionStore();
const { sendAgentMessage, error } = useObserver();
const project = computed(() => projects.projects.get(props.projectId));
const agents = computed(() => (project.value?.agents ?? []).filter((a) => a.agent_id));
let nextId = 0;
const links = ref([{ id: nextId++, source: "", target: "" }]);
const content = ref("");
const mode = ref<"sync" | "async">("sync");
const schedule = ref<"parallel" | "sequence">("parallel");
const running = ref(false);
let runVersion = 0;
type Status = "waiting" | "running" | "prepared" | "replied" | "failed" | "unknown" | "skipped";
const results = ref<
  { id: number; source: AgentTarget; target: AgentTarget; status: Status; detail: string }[]
>([]);

function agentTarget(id: string): AgentTarget | null {
  const agent = agents.value.find((a) => a.agent_id === id);
  return agent ? { projectId: props.projectId, agentId: id, agentName: agent.name } : null;
}
const canRun = computed(
  () =>
    !running.value &&
    connection.state === "connected" &&
    project.value?.status === "active" &&
    content.value.trim() &&
    links.value.every(
      (link) => link.source !== link.target && agentTarget(link.source) && agentTarget(link.target),
    ),
);

const disabledReason = computed(() => {
  if (running.value) return t("communication.waitingHint");
  if (connection.state !== "connected") return t("communication.disconnected");
  if (project.value?.status !== "active") return t("communication.inactive");
  if (!content.value.trim()) return t("communication.enterContent");
  if (!canRun.value) return t("communication.chooseLinks");
  return "";
});

function stopWaiting() {
  runVersion++;
  for (const row of results.value) {
    if (row.status === "running") {
      row.status = "unknown";
      row.detail = t("communication.stoppedHint");
    } else if (row.status === "waiting") row.status = "skipped";
  }
  running.value = false;
}
onBeforeUnmount(stopWaiting);

async function run() {
  if (!canRun.value) return;
  const version = ++runVersion;
  const text = content.value;
  const asyncMode = mode.value === "async";
  const sequential = schedule.value === "sequence";
  results.value = links.value.map((link) => ({
    id: link.id,
    source: agentTarget(link.source)!,
    target: agentTarget(link.target)!,
    status: "waiting",
    detail: "",
  }));
  running.value = true;
  async function send(row: (typeof results.value)[number]) {
    row.status = "running";
    try {
      const response = await sendAgentMessage(row.source, row.target, text, asyncMode);
      if (version !== runVersion) return;
      if (!response) {
        row.status = "unknown";
        row.detail = error.value ?? t("communication.unknownHint");
      } else if (!response.success) {
        row.status = "failed";
        row.detail = [
          response.error === "source_ability_missing"
            ? t("communication.missingAbility", { agent: row.source.agentName })
            : response.error?.startsWith("message_queue_blocked:")
              ? `${t("communication.queueBlockedHint")}\n${response.error}`
              : (response.error ?? t("common.validationFailed")),
          response.error_detail,
          JSON.stringify(response.data, null, 2),
        ]
          .filter(Boolean)
          .join("\n");
      } else {
        row.status = asyncMode ? "prepared" : "replied";
        row.detail = JSON.stringify(response.data, null, 2);
      }
    } catch (cause) {
      if (version !== runVersion) return;
      row.status = "unknown";
      row.detail = String(cause);
    }
  }
  try {
    if (sequential) {
      for (const row of results.value) {
        await send(row);
        if (version !== runVersion) break;
        if (row.status === "failed" || row.status === "unknown") {
          for (const pending of results.value)
            if (pending.status === "waiting") pending.status = "skipped";
          break;
        }
      }
    } else {
      await Promise.all(results.value.map(send));
    }
  } finally {
    if (version === runVersion) running.value = false;
  }
}
</script>

<template>
  <details class="communication-panel">
    <summary>{{ t("communication.title") }}</summary>
    <p>{{ t("communication.hint") }}</p>
    <fieldset :disabled="running">
      <div v-for="(link, index) in links" :key="link.id" class="communication-link">
        <label
          >{{ t("communication.source", { index: index + 1 }) }}
          <select v-model="link.source">
            <option value="" disabled>{{ t("communication.choose") }}</option>
            <option v-for="agent in agents" :key="agent.agent_id" :value="agent.agent_id">
              {{ agent.name }} · {{ agent.cluster_id }} · {{ agent.agent_id }}
            </option>
          </select>
        </label>
        <label
          >{{ t("communication.target") }}
          <select v-model="link.target">
            <option value="" disabled>{{ t("communication.choose") }}</option>
            <option
              v-for="agent in agents"
              :key="agent.agent_id"
              :value="agent.agent_id"
              :disabled="agent.agent_id === link.source"
            >
              {{ agent.name }} · {{ agent.cluster_id }} · {{ agent.agent_id }}
            </option>
          </select>
        </label>
        <button
          type="button"
          class="btn-secondary"
          :disabled="links.length === 1"
          @click="links.splice(index, 1)"
        >
          {{ t("communication.remove") }}
        </button>
      </div>
      <button
        type="button"
        class="btn-secondary"
        @click="links.push({ id: nextId++, source: '', target: '' })"
      >
        {{ t("communication.add") }}
      </button>
      <label class="communication-field"
        >{{ t("communication.content") }}<textarea v-model="content" rows="3" />
      </label>
      <div class="communication-link">
        <label
          >{{ t("communication.mode")
          }}<select v-model="mode">
            <option value="sync">{{ t("communication.sync") }}</option>
            <option value="async">{{ t("communication.async") }}</option>
          </select></label
        >
        <label
          >{{ t("communication.schedule")
          }}<select v-model="schedule">
            <option value="parallel">{{ t("communication.parallel") }}</option>
            <option value="sequence">{{ t("communication.sequence") }}</option>
          </select></label
        >
        <button type="button" class="btn-primary" :disabled="!canRun" @click="run">
          {{ t(running ? "communication.running" : "communication.run") }}
        </button>
      </div>
    </fieldset>
    <p v-if="disabledReason" role="status">{{ disabledReason }}</p>
    <button v-if="running" type="button" class="btn-secondary" @click="stopWaiting">
      {{ t("communication.stopWaiting") }}
    </button>
    <p>
      {{ t(schedule === "sequence" ? "communication.sequenceHint" : "communication.parallelHint") }}
    </p>
    <p v-if="mode === 'async'">{{ t("communication.asyncHint") }}</p>
    <div aria-live="polite">
      <p v-if="results.some((row) => row.status === 'unknown')" role="alert">
        {{ t("communication.unknownHint") }}
      </p>
      <article v-for="row in results" :key="row.id" class="communication-result">
        <strong
          >{{ row.source.agentName }} → {{ row.target.agentName }} ·
          {{ t(`communication.status.${row.status}`) }}</strong
        >
        <pre v-if="row.detail">{{ row.detail }}</pre>
      </article>
    </div>
  </details>
</template>

<style scoped>
.communication-panel {
  margin-block: var(--spacing-4);
  font-size: var(--font-ui-note);
}
.communication-link {
  display: flex;
  flex-wrap: wrap;
  align-items: end;
  gap: var(--spacing-3);
  margin-block: var(--spacing-3);
}
.communication-field,
label {
  display: flex;
  flex-direction: column;
  gap: var(--spacing-2);
}
select,
textarea {
  color: inherit;
  background: transparent;
  border: 1px solid currentColor;
  border-radius: var(--radius-md);
  padding: var(--spacing-2);
}
fieldset {
  border: 0;
  padding: 0;
  min-width: 0;
}
.communication-result {
  margin-block: var(--spacing-3);
}
pre {
  white-space: pre-wrap;
  overflow-wrap: anywhere;
}
</style>
