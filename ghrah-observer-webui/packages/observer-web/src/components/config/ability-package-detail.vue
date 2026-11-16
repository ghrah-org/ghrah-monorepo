<!--
SPDX-FileCopyrightText: 2026 chenxya <chenxya@ghrah.org>

SPDX-License-Identifier: Apache-2.0
-->

<script setup lang="ts">
import type { VersionProfileRecord } from "@ghrah/observer-core";
import type {
  AbilityOperationMemberView,
  AbilityPackageVersionProfileView,
  AbilitySkillMemberView,
} from "@ghrah/protocol";
import { computed, ref, watch } from "vue";
import { useI18n } from "vue-i18n";
import UiButton from "@/components/ui/button.vue";
import CollapsibleSection from "@/components/ui/collapsible-section.vue";

const props = defineProps<{
  record: VersionProfileRecord | null;
  mcpProbeEnabled: boolean;
}>();

defineEmits<{ retry: [] }>();

const { t } = useI18n();

type MemberTab = "tools" | "skills" | "mcp";

const activeTab = ref<MemberTab>("tools");

const profile = computed(() =>
  props.record && !props.record.loading && !props.record.failed ? props.record.profile : null,
);

const counts = computed<Record<MemberTab, number>>(() => {
  const view = profile.value;
  if (!view) return { tools: 0, skills: 0, mcp: 0 };
  return {
    tools: view.operations.length,
    skills: view.skills.length,
    mcp: view.mcp ? 1 : 0,
  };
});

/**
 * 默认落在第一个非空分类：纯技能包不该先显示「没有工具成员」。
 * 全空时回落到工具页签，避免用「不是 MCP 包」当作默认内容。
 * 只在当前页签为空时改选，避免刷新后把用户手动切走的页签拽回来。
 */
watch(
  profile,
  (view) => {
    if (!view) return;
    if (counts.value[activeTab.value] > 0) return;
    if (counts.value.tools > 0) activeTab.value = "tools";
    else if (counts.value.skills > 0) activeTab.value = "skills";
    else if (counts.value.mcp > 0) activeTab.value = "mcp";
    else activeTab.value = "tools";
  },
  { immediate: true },
);

const tabs = computed(() => [
  { id: "tools" as const, label: t("assembly.catalog.detail.tools"), count: counts.value.tools },
  { id: "skills" as const, label: t("assembly.catalog.detail.skills"), count: counts.value.skills },
  { id: "mcp" as const, label: t("assembly.catalog.detail.mcp"), count: counts.value.mcp },
]);

interface SchemaChip {
  name: string;
  type: string;
  required: boolean;
}

/** JSON Schema 的 ``properties`` 摊平成「字段 + 类型」小块，比「object, 必填 …」好扫。 */
function schemaChips(schema: Record<string, unknown>): SchemaChip[] {
  const raw = schema.properties;
  const required = new Set(
    Array.isArray(schema.required)
      ? schema.required.filter((item): item is string => typeof item === "string")
      : [],
  );
  const chips: SchemaChip[] =
    raw && typeof raw === "object"
      ? Object.entries(raw as Record<string, unknown>).map(([name, value]) => {
          const node = (value && typeof value === "object" ? value : {}) as Record<string, unknown>;
          return { name, type: typeLabel(node), required: required.has(name) };
        })
      : [];
  // 只声明了 required、没给 properties 时，别把必填字段丢掉。
  for (const name of required) {
    if (!chips.some((chip) => chip.name === name)) {
      chips.push({ name, type: "any", required: true });
    }
  }
  return chips;
}

function typeLabel(node: Record<string, unknown>): string {
  const type = typeof node.type === "string" ? node.type : "";
  if (type === "array") {
    const items = node.items;
    const inner =
      items && typeof items === "object" ? (items as Record<string, unknown>).type : null;
    return typeof inner === "string" ? `${inner}[]` : "array";
  }
  if (!type) return node.properties ? "object" : "any";
  return type;
}

/** 有字段就报「N 项 · 必填 M」；没有字段时退回根类型，别只说「没有字段」。 */
function schemaSummary(schema: Record<string, unknown>, chips: SchemaChip[]): string {
  if (chips.length > 0) {
    return t("assembly.catalog.detail.schemaSummary", {
      count: chips.length,
      required: chips.filter((chip) => chip.required).length,
    });
  }
  const type = typeLabel(schema);
  if (type && type !== "object") return type;
  return t("assembly.catalog.detail.schemaEmpty");
}

interface SchemaView {
  summary: string;
  chips: SchemaChip[];
  hidden: number;
  expanded: boolean;
  over: boolean;
}

interface OperationView {
  op: AbilityOperationMemberView;
  input: SchemaView;
  output: SchemaView;
}

const SCHEMA_CHIP_LIMIT = 8;
const expandedSchemas = ref(new Set<string>());

function toggleSchema(key: string): void {
  const next = new Set(expandedSchemas.value);
  if (next.has(key)) next.delete(key);
  else next.add(key);
  expandedSchemas.value = next;
}

function schemaView(op: AbilityOperationMemberView, side: "input" | "output"): SchemaView {
  const schema = side === "input" ? op.input_schema : op.output_schema;
  const all = schemaChips(schema);
  const key = `${op.key}:${side}`;
  const over = all.length > SCHEMA_CHIP_LIMIT;
  const expanded = over && expandedSchemas.value.has(key);
  return {
    summary: schemaSummary(schema, all),
    chips: over && !expanded ? all.slice(0, SCHEMA_CHIP_LIMIT) : all,
    hidden: over && !expanded ? all.length - SCHEMA_CHIP_LIMIT : 0,
    expanded,
    over,
  };
}

/** 字段多时先露 8 个，剩下的收进「+N」，避免一个工具占掉半屏。 */
const operationViews = computed<OperationView[]>(() =>
  (profile.value?.operations ?? []).map((op) => ({
    op,
    input: schemaView(op, "input"),
    output: schemaView(op, "output"),
  })),
);

function timeoutLabel(ms: number): string {
  if (ms > 0 && ms % 1000 === 0) {
    return t("assembly.catalog.detail.timeoutSeconds", { count: ms / 1000 });
  }
  return t("assembly.catalog.detail.timeoutMs", { count: ms });
}

function cancellationLabel(value: AbilityOperationMemberView["execution"]["cancellation"]): string {
  return value === "cooperative"
    ? t("assembly.catalog.detail.cancelOn")
    : t("assembly.catalog.detail.cancelOff");
}

function failureText(code: string | null): string {
  if (code === "not_found") return t("assembly.catalog.detail.notFound");
  if (code === "unavailable" || code === "capability_unavailable") {
    return t("assembly.catalog.detail.unavailable");
  }
  if (code === "access_denied") return t("assembly.catalog.detail.accessDenied");
  return t("assembly.catalog.detail.failed");
}

function originText(origin: AbilityPackageVersionProfileView["origin"]): string {
  if (origin === "builtin") return t("assembly.catalog.origin.builtin");
  return t("assembly.catalog.origin.installed");
}

function problemText(problem: string): string {
  if (problem === "definition_missing") return t("assembly.versions.missingDefinition");
  if (problem.startsWith("definition_unreadable")) {
    return t("assembly.versions.unreadableDefinition");
  }
  return problem;
}

function definitionOriginText(
  origin: AbilityPackageVersionProfileView["definition_origin"],
): string {
  if (origin === "source") return t("assembly.catalog.detail.definitionSource");
  if (origin === "mcp") return t("assembly.catalog.detail.definitionMcp");
  return t("assembly.catalog.detail.notProvided");
}

function skillAttachments(skill: AbilitySkillMemberView): {
  kind: string;
  label: string;
  items: { key: string; path: string }[];
}[] {
  return [
    { kind: "assets", label: t("assembly.catalog.detail.assets"), items: skill.assets },
    {
      kind: "references",
      label: t("assembly.catalog.detail.references"),
      items: skill.references,
    },
    { kind: "scripts", label: t("assembly.catalog.detail.scripts"), items: skill.scripts },
  ].filter((group) => group.items.length > 0);
}

function showProbeDisabled(view: AbilityPackageVersionProfileView | null): boolean {
  if (!view || props.mcpProbeEnabled) return false;
  return view.definition_origin === "mcp" || view.mcp !== null;
}
</script>

<template>
  <div class="ability-detail">
    <p v-if="!record || record.loading" class="ability-detail-note">
      {{ t("assembly.catalog.detail.loading") }}
    </p>
    <div v-else-if="record.failed" class="ability-detail-alert" role="alert">
      <p>{{ failureText(record.errorCode) }}</p>
      <UiButton type="button" variant="secondary" size="sm" @click="$emit('retry')">
        {{ t("assembly.retry") }}
      </UiButton>
    </div>
    <template v-else-if="profile">
      <div
        class="ability-member-tabs"
        role="tablist"
        :aria-label="t('assembly.catalog.detail.members')"
      >
        <button
          v-for="tab in tabs"
          :key="tab.id"
          type="button"
          role="tab"
          class="ability-member-tab"
          :aria-selected="activeTab === tab.id"
          @click="activeTab = tab.id"
        >
          {{ tab.label }}<span class="ability-member-tab-count">{{ tab.count }}</span>
        </button>
        <span class="ability-member-meta">
          <span
            >{{ originText(profile.origin) }} ·
            {{ definitionOriginText(profile.definition_origin) }}</span
          >
          <span v-if="profile.lock_state && profile.lock_state !== 'builtin'">
            {{ t("assembly.catalog.lock", { state: profile.lock_state }) }}
          </span>
          <span v-if="profile.shadowed_builtin" class="ability-chip">
            {{ t("assembly.catalog.shadowed") }}
          </span>
          <span v-if="profile.problem" class="ability-chip" data-tone="off">
            {{ problemText(profile.problem) }}
          </span>
        </span>
      </div>

      <div role="tabpanel">
        <template v-if="activeTab === 'tools'">
          <p v-if="!profile.operations.length" class="ability-detail-note">
            {{ t("assembly.catalog.detail.toolsEmpty") }}
          </p>
          <div v-else class="ability-ops">
            <article v-for="row in operationViews" :key="row.op.key" class="ability-op">
              <header class="ability-op-head">
                <code class="ability-op-key">{{ row.op.key }}</code>
                <h4 class="ability-op-title">{{ row.op.title }}</h4>
                <span class="ability-op-badges">
                  <span v-if="row.op.mcp" class="ability-chip ability-chip-mcp">
                    MCP {{ row.op.mcp.server }}/{{ row.op.mcp.tool }}
                  </span>
                  <span class="ability-chip" :data-tone="row.op.expose ? 'on' : 'off'">
                    {{
                      row.op.expose
                        ? t("assembly.catalog.detail.exposeOn")
                        : t("assembly.catalog.detail.exposeOff")
                    }}
                  </span>
                </span>
              </header>
              <p v-if="row.op.description" class="ability-op-desc">{{ row.op.description }}</p>
              <dl class="ability-op-facts">
                <div>
                  <dt>{{ t("assembly.catalog.detail.input") }}</dt>
                  <dd>
                    <span class="ability-op-note">{{ row.input.summary }}</span>
                    <span
                      v-for="chip in row.input.chips"
                      :key="chip.name"
                      class="ability-schema-chip"
                      :data-required="chip.required"
                    >
                      {{ chip.name }} <em>{{ chip.type }}</em>
                    </span>
                    <button
                      v-if="row.input.over"
                      type="button"
                      class="ability-chip ability-chip-action"
                      @click="toggleSchema(`${row.op.key}:input`)"
                    >
                      {{
                        row.input.expanded
                          ? t("assembly.catalog.detail.lessChips")
                          : `+${row.input.hidden}`
                      }}
                    </button>
                  </dd>
                </div>
                <div>
                  <dt>{{ t("assembly.catalog.detail.output") }}</dt>
                  <dd>
                    <span class="ability-op-note">{{ row.output.summary }}</span>
                    <span
                      v-for="chip in row.output.chips"
                      :key="chip.name"
                      class="ability-schema-chip"
                      :data-required="chip.required"
                    >
                      {{ chip.name }} <em>{{ chip.type }}</em>
                    </span>
                    <button
                      v-if="row.output.over"
                      type="button"
                      class="ability-chip ability-chip-action"
                      @click="toggleSchema(`${row.op.key}:output`)"
                    >
                      {{
                        row.output.expanded
                          ? t("assembly.catalog.detail.lessChips")
                          : `+${row.output.hidden}`
                      }}
                    </button>
                  </dd>
                </div>
                <div>
                  <dt>{{ t("assembly.catalog.detail.effectsLabel") }}</dt>
                  <dd>
                    <span v-if="!row.op.effects.length" class="ability-op-note">
                      {{ t("assembly.catalog.detail.none") }}
                    </span>
                    <span v-for="effect in row.op.effects" :key="effect" class="ability-chip">
                      {{ effect }}
                    </span>
                  </dd>
                </div>
                <div>
                  <dt>{{ t("assembly.catalog.detail.executionLabel") }}</dt>
                  <dd>
                    <span class="ability-chip">{{ row.op.execution.mode }}</span>
                    <span class="ability-chip">{{
                      timeoutLabel(row.op.execution.timeout_ms)
                    }}</span>
                    <span class="ability-chip">
                      {{ cancellationLabel(row.op.execution.cancellation) }}
                    </span>
                    <span class="ability-chip">
                      {{
                        t("assembly.catalog.detail.revokeChip", {
                          value: row.op.execution.on_revoke,
                        })
                      }}
                    </span>
                    <span class="ability-chip">
                      {{
                        t("assembly.catalog.detail.retryChip", { value: row.op.execution.retry })
                      }}
                    </span>
                  </dd>
                </div>
                <div v-if="row.op.entry">
                  <dt>{{ t("assembly.catalog.detail.entryLabel") }}</dt>
                  <dd class="ability-op-entry">
                    <code>{{ row.op.entry.path }}</code>
                    <span v-if="row.op.entry.media_type">{{ row.op.entry.media_type }}</span>
                    <span v-if="row.op.entry.size != null">
                      {{ t("assembly.catalog.detail.size", { size: row.op.entry.size }) }}
                    </span>
                    <span v-if="row.op.entry.digest">
                      {{ t("assembly.catalog.detail.digest", { digest: row.op.entry.digest }) }}
                    </span>
                  </dd>
                </div>
                <div v-if="row.op.tags.length">
                  <dt>{{ t("assembly.catalog.detail.tags") }}</dt>
                  <dd>
                    <span v-for="tag in row.op.tags" :key="tag" class="ability-chip">
                      {{ tag }}
                    </span>
                  </dd>
                </div>
              </dl>
            </article>
          </div>
        </template>

        <template v-else-if="activeTab === 'skills'">
          <p v-if="!profile.skills.length" class="ability-detail-note">
            {{ t("assembly.catalog.detail.skillsEmpty") }}
          </p>
          <div v-else class="ability-ops">
            <article v-for="skill in profile.skills" :key="skill.key" class="ability-op">
              <header class="ability-op-head">
                <code class="ability-op-key">{{ skill.key }}</code>
                <h4 class="ability-op-title">{{ skill.title }}</h4>
              </header>
              <p v-if="skill.description" class="ability-op-desc">{{ skill.description }}</p>
              <dl class="ability-op-facts">
                <div>
                  <dt>{{ t("assembly.catalog.detail.body") }}</dt>
                  <dd class="ability-op-entry">
                    <code>{{ skill.body.path }}</code>
                    <span v-if="skill.body.media_type">{{ skill.body.media_type }}</span>
                    <span v-if="skill.body.size != null">
                      {{ t("assembly.catalog.detail.size", { size: skill.body.size }) }}
                    </span>
                    <span v-if="skill.body.digest">
                      {{ t("assembly.catalog.detail.digest", { digest: skill.body.digest }) }}
                    </span>
                  </dd>
                </div>
                <div v-for="group in skillAttachments(skill)" :key="group.kind">
                  <dt>{{ group.label }}</dt>
                  <dd>
                    <span v-for="item in group.items" :key="item.key" class="ability-chip">
                      {{ item.path }}
                    </span>
                  </dd>
                </div>
              </dl>
            </article>
          </div>
        </template>

        <template v-else>
          <dl v-if="profile.mcp" class="ability-op-facts ability-op-facts-solo">
            <div>
              <dt>{{ t("assembly.catalog.detail.mcp") }}</dt>
              <dd>
                <span class="ability-chip ability-chip-mcp">{{ profile.mcp.server }}</span>
                <span class="ability-chip">{{ profile.mcp.fetched_at }}</span>
                <span class="ability-chip" :data-tone="profile.mcp.stale ? 'off' : 'on'">
                  {{
                    profile.mcp.stale
                      ? t("assembly.catalog.detail.mcpStale")
                      : t("assembly.catalog.detail.mcpFresh")
                  }}
                </span>
              </dd>
            </div>
            <div v-if="profile.mcp.last_error">
              <dt>{{ t("assembly.catalog.detail.problem") }}</dt>
              <dd>
                <span class="ability-chip" data-tone="off">{{ profile.mcp.last_error }}</span>
              </dd>
            </div>
          </dl>
          <p v-else-if="profile.definition_origin === 'source'" class="ability-detail-note">
            {{ t("assembly.catalog.detail.mcpNotPackage") }}
          </p>
          <p v-else-if="profile.definition_origin === 'mcp'" class="ability-detail-note">
            {{ t("assembly.catalog.detail.mcpSnapshotAbsent") }}
          </p>
          <p v-else class="ability-detail-note">
            {{ t("assembly.catalog.detail.mcpNotProvided") }}
          </p>
          <p v-if="showProbeDisabled(profile)" class="ability-detail-note">
            {{ t("assembly.catalog.detail.mcpProbeDisabled") }}
          </p>
        </template>
      </div>

      <CollapsibleSection :title="t('assembly.catalog.detail.verify')" default-open>
        <p v-if="!profile.verify" class="ability-detail-note">
          {{ t("assembly.catalog.detail.verifyAbsent") }}
        </p>
        <div v-else class="ability-verify">
          <p>
            {{
              profile.verify.ok
                ? t("assembly.catalog.detail.verifyOk")
                : t("assembly.catalog.detail.verifyFailed")
            }}
          </p>
          <p v-if="profile.verify.code">
            {{ t("assembly.catalog.detail.verifyCode", { code: profile.verify.code }) }}
          </p>
          <p v-if="profile.verify.message">
            {{ t("assembly.catalog.detail.verifyMessage", { message: profile.verify.message }) }}
          </p>
          <p v-if="profile.verify.package_digest">
            {{
              t("assembly.catalog.detail.verifyPackageDigest", {
                digest: profile.verify.package_digest,
              })
            }}
          </p>
          <p v-if="profile.verify.actual_digest">
            {{
              t("assembly.catalog.detail.verifyActualDigest", {
                digest: profile.verify.actual_digest,
              })
            }}
          </p>
          <div v-if="profile.verify.added.length">
            <p>{{ t("assembly.catalog.detail.verifyAdded") }}</p>
            <ul>
              <li v-for="path in profile.verify.added" :key="path">
                <code>{{ path }}</code>
              </li>
            </ul>
          </div>
          <div v-if="profile.verify.removed.length">
            <p>{{ t("assembly.catalog.detail.verifyRemoved") }}</p>
            <ul>
              <li v-for="path in profile.verify.removed" :key="path">
                <code>{{ path }}</code>
              </li>
            </ul>
          </div>
          <div v-if="profile.verify.changed.length">
            <p>{{ t("assembly.catalog.detail.verifyChanged") }}</p>
            <ul>
              <li v-for="path in profile.verify.changed" :key="path">
                <code>{{ path }}</code>
              </li>
            </ul>
          </div>
        </div>
      </CollapsibleSection>
    </template>
  </div>
</template>

<style scoped>
.ability-detail {
  display: flex;
  min-width: 0;
  flex-direction: column;
  gap: 0.75rem;
}

.ability-detail-note,
.ability-verify,
.ability-verify p,
.ability-detail-alert p {
  margin: 0;
}

.ability-detail-note,
.ability-verify {
  color: var(--color-text-muted);
  font-size: 0.75rem;
  line-height: var(--leading-note);
}

.ability-member-tabs {
  display: flex;
  flex-wrap: wrap;
  align-items: center;
  gap: 0.125rem;
  padding: 0.125rem;
  border-radius: var(--radius-md);
  background: var(--color-surface-2);
}

.ability-member-tab {
  display: inline-flex;
  align-items: center;
  gap: 0.3125rem;
  padding: 0.1875rem 0.5rem;
  border: none;
  border-radius: 0.25rem;
  background: transparent;
  color: var(--color-text-muted);
  font-size: 0.75rem;
  cursor: pointer;
}

.ability-member-tab:hover {
  color: var(--color-text);
}

.ability-member-tab[aria-selected="true"] {
  background: var(--color-surface);
  color: var(--color-text);
  font-weight: 600;
}

.ability-member-tab-count {
  color: var(--color-text-subtle);
  font-variant-numeric: tabular-nums;
}

.ability-member-tab[aria-selected="true"] .ability-member-tab-count {
  color: var(--color-text-muted);
}

.ability-member-meta {
  display: inline-flex;
  flex-wrap: wrap;
  align-items: center;
  gap: 0.25rem;
  margin-inline-start: auto;
  padding-inline-end: 0.375rem;
  color: var(--color-text-subtle);
  font-size: 0.6875rem;
}

/* ── 每个成员一块：标识行 + 描述 + 标签-值事实表 ── */
.ability-ops {
  display: flex;
  flex-direction: column;
}

.ability-op {
  display: flex;
  min-width: 0;
  flex-direction: column;
  gap: 0.375rem;
  padding: 0.75rem 0;
  border-top: 1px solid var(--color-border);
}

.ability-ops > .ability-op:first-child {
  padding-top: 0.25rem;
  border-top: none;
}

.ability-op-head {
  display: flex;
  min-width: 0;
  flex-wrap: wrap;
  align-items: center;
  gap: 0.5rem;
}

.ability-op-key {
  padding: 0 0.3125rem;
  border: 1px solid var(--color-border);
  border-radius: 0.25rem;
  background: var(--color-surface-2);
  color: var(--color-text-muted);
  font-size: 0.6875rem;
  line-height: 1.5;
}

.ability-op-title {
  margin: 0;
  color: var(--color-text);
  font-size: 0.875rem;
  font-weight: 600;
}

.ability-op-badges {
  display: inline-flex;
  flex-wrap: wrap;
  align-items: center;
  gap: 0.25rem;
  margin-inline-start: auto;
}

.ability-op-desc {
  margin: 0;
  color: var(--color-text-muted);
  font-size: 0.75rem;
  line-height: var(--leading-note);
}

.ability-op-facts {
  display: flex;
  flex-direction: column;
  gap: 0.3125rem;
  margin: 0.125rem 0 0;
}

.ability-op-facts > div {
  display: grid;
  grid-template-columns: 3.5rem minmax(0, 1fr);
  align-items: start;
  gap: 0.75rem;
}

.ability-op-facts-solo {
  margin: 0;
}

.ability-op-facts dt {
  padding-top: 0.0625rem;
  color: var(--color-text-subtle);
  font-size: 0.6875rem;
  line-height: 1.5;
}

.ability-op-facts dd {
  display: flex;
  min-width: 0;
  flex-wrap: wrap;
  align-items: center;
  gap: 0.25rem;
  margin: 0;
}

.ability-op-note {
  color: var(--color-text-subtle);
  font-size: 0.6875rem;
}

.ability-op-entry {
  gap: 0.375rem;
  color: var(--color-text-subtle);
  font-size: 0.6875rem;
}

.ability-op-entry code {
  color: var(--color-text-muted);
  font-size: 0.6875rem;
}

/* ── 小块标签 ── */
.ability-chip {
  display: inline-flex;
  align-items: center;
  gap: 0.1875rem;
  padding: 0 0.3125rem;
  border: 1px solid var(--color-border);
  border-radius: 0.25rem;
  background: var(--color-surface-2);
  color: var(--color-text-muted);
  font-size: 0.6875rem;
  line-height: 1.5;
  white-space: nowrap;
}

.ability-chip-mcp {
  border-color: color-mix(in srgb, var(--color-ac-converse) 45%, var(--color-border));
  background: color-mix(in srgb, var(--color-ac-converse) 14%, var(--color-surface));
  color: var(--color-ac-converse);
}

.ability-chip[data-tone="on"] {
  border-color: color-mix(in srgb, var(--color-success) 45%, var(--color-border));
  background: color-mix(in srgb, var(--color-success) 12%, var(--color-surface));
  color: var(--color-success);
}

.ability-chip-action {
  cursor: pointer;
  border-style: dashed;
}

.ability-chip-action:hover {
  border-color: var(--color-border-strong);
  color: var(--color-text);
}

.ability-chip[data-tone="off"] {
  border-color: var(--color-border-strong);
  color: var(--color-text-subtle);
}

.ability-schema-chip {
  display: inline-flex;
  align-items: baseline;
  gap: 0.1875rem;
  padding: 0 0.3125rem;
  border: 1px solid var(--color-border);
  border-radius: 0.25rem;
  color: var(--color-text-muted);
  font-size: 0.6875rem;
  line-height: 1.5;
  white-space: nowrap;
}

.ability-schema-chip em {
  color: var(--color-text-subtle);
  font-style: normal;
}

.ability-schema-chip[data-required="true"] {
  border-color: var(--color-border-strong);
  color: var(--color-text);
  font-weight: 600;
}

.ability-verify {
  display: flex;
  flex-direction: column;
  gap: 0.25rem;
}

.ability-verify ul {
  display: flex;
  flex-direction: column;
  gap: 0.125rem;
  margin: 0.25rem 0 0;
  padding: 0;
  list-style: none;
}

.ability-detail-alert {
  display: flex;
  min-width: 0;
  flex-wrap: wrap;
  align-items: center;
  gap: 0.5rem 0.75rem;
  color: var(--color-danger);
  font-size: 0.75rem;
}
</style>
