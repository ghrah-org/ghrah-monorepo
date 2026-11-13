<!--
SPDX-FileCopyrightText: 2026 chenxya <chenxya@ghrah.org>

SPDX-License-Identifier: Apache-2.0
-->

<script setup lang="ts">
import type { VersionProfileRecord } from "@ghrah/observer-core";
import type {
  AbilityNamedResourceView,
  AbilityPackageVersionProfileView,
  AbilitySkillMemberView,
} from "@ghrah/protocol";
import { computed } from "vue";
import { useI18n } from "vue-i18n";
import UiButton from "@/components/ui/button.vue";
import CollapsibleSection from "@/components/ui/collapsible-section.vue";

const props = defineProps<{
  record: VersionProfileRecord | null;
  mcpProbeEnabled: boolean;
}>();

defineEmits<{ retry: [] }>();

const { t } = useI18n();

const profile = computed(() =>
  props.record && !props.record.loading && !props.record.failed ? props.record.profile : null,
);

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

function schemaSummary(schema: Record<string, unknown>): string {
  const type = typeof schema.type === "string" ? schema.type : "";
  const required = Array.isArray(schema.required)
    ? schema.required.filter((item): item is string => typeof item === "string")
    : [];
  if (!type && required.length === 0) return t("assembly.catalog.detail.schemaEmpty");
  const parts: string[] = [];
  if (type) parts.push(t("assembly.catalog.detail.schemaType", { type }));
  if (required.length > 0) {
    parts.push(t("assembly.catalog.detail.schemaRequired", { fields: required.join(", ") }));
  }
  return parts.join(", ");
}

function attachmentGroups(skill: AbilitySkillMemberView): {
  kind: string;
  label: string;
  empty: string;
  items: AbilityNamedResourceView[];
}[] {
  return [
    {
      kind: "assets",
      label: t("assembly.catalog.detail.assets"),
      empty: t("assembly.catalog.detail.assetsEmpty"),
      items: skill.assets,
    },
    {
      kind: "references",
      label: t("assembly.catalog.detail.references"),
      empty: t("assembly.catalog.detail.referencesEmpty"),
      items: skill.references,
    },
    {
      kind: "scripts",
      label: t("assembly.catalog.detail.scripts"),
      empty: t("assembly.catalog.detail.scriptsEmpty"),
      items: skill.scripts,
    },
  ];
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
      <dl class="ability-facts">
        <div v-if="profile.title">
          <dt>{{ t("assembly.catalog.detail.title") }}</dt>
          <dd>{{ profile.title }}</dd>
        </div>
        <div v-if="profile.description">
          <dt>{{ t("assembly.catalog.detail.description") }}</dt>
          <dd>{{ profile.description }}</dd>
        </div>
        <div>
          <dt>{{ t("assembly.catalog.detail.origin") }}</dt>
          <dd>{{ originText(profile.origin) }}</dd>
        </div>
        <div>
          <dt>{{ t("assembly.catalog.detail.definitionOrigin") }}</dt>
          <dd>{{ definitionOriginText(profile.definition_origin) }}</dd>
        </div>
        <div>
          <dt>{{ t("assembly.catalog.lock", { state: profile.lock_state }) }}</dt>
          <dd>{{ profile.lock_state }}</dd>
        </div>
        <div v-if="profile.shadowed_builtin">
          <dt>{{ t("assembly.catalog.shadowed") }}</dt>
          <dd>{{ t("assembly.catalog.shadowed") }}</dd>
        </div>
        <div v-if="profile.problem">
          <dt>{{ t("assembly.catalog.detail.problem") }}</dt>
          <dd>{{ problemText(profile.problem) }}</dd>
        </div>
      </dl>

      <CollapsibleSection :title="t('assembly.catalog.detail.tools')" default-open>
        <template #actions>
          {{ t("assembly.catalog.detail.count", { count: profile.operations.length }) }}
        </template>
        <p v-if="!profile.operations.length" class="ability-detail-note">
          {{ t("assembly.catalog.detail.toolsEmpty") }}
        </p>
        <ul v-else class="ability-members">
          <li v-for="operation in profile.operations" :key="operation.key">
            <p>
              <code>{{ operation.key }}</code>
              {{ operation.title }}
            </p>
            <p v-if="operation.description" class="ability-detail-note">
              {{ operation.description }}
            </p>
            <p>
              {{
                t("assembly.catalog.detail.schemaInput", {
                  summary: schemaSummary(operation.input_schema),
                })
              }}
            </p>
            <p>
              {{
                t("assembly.catalog.detail.schemaOutput", {
                  summary: schemaSummary(operation.output_schema),
                })
              }}
            </p>
            <p v-if="operation.effects.length">
              {{ t("assembly.catalog.detail.effects", { effects: operation.effects.join(", ") }) }}
            </p>
            <p>
              {{
                operation.expose
                  ? t("assembly.catalog.detail.exposeOn")
                  : t("assembly.catalog.detail.exposeOff")
              }}
            </p>
            <p>
              {{
                t("assembly.catalog.detail.execution", {
                  mode: operation.execution.mode,
                  timeout: operation.execution.timeout_ms,
                  cancellation: operation.execution.cancellation,
                  onRevoke: operation.execution.on_revoke,
                  retry: operation.execution.retry,
                })
              }}
            </p>
            <p v-if="operation.entry">
              {{ t("assembly.catalog.detail.entry", { path: operation.entry.path }) }}
              <span v-if="operation.entry.media_type">{{ operation.entry.media_type }}</span>
              <span v-if="operation.entry.size != null">
                {{ t("assembly.catalog.detail.size", { size: operation.entry.size }) }}
              </span>
              <span v-if="operation.entry.digest">
                {{ t("assembly.catalog.detail.digest", { digest: operation.entry.digest }) }}
              </span>
            </p>
            <p v-if="operation.mcp">
              {{
                t("assembly.catalog.detail.mcpMember", {
                  server: operation.mcp.server,
                  tool: operation.mcp.tool,
                })
              }}
            </p>
          </li>
        </ul>
      </CollapsibleSection>

      <CollapsibleSection :title="t('assembly.catalog.detail.skills')" default-open>
        <template #actions>
          {{ t("assembly.catalog.detail.count", { count: profile.skills.length }) }}
        </template>
        <p v-if="!profile.skills.length" class="ability-detail-note">
          {{ t("assembly.catalog.detail.skillsEmpty") }}
        </p>
        <ul v-else class="ability-members">
          <li v-for="skill in profile.skills" :key="skill.key">
            <p>
              <code>{{ skill.key }}</code>
              {{ skill.title }}
            </p>
            <p v-if="skill.description" class="ability-detail-note">{{ skill.description }}</p>
            <p>
              {{ t("assembly.catalog.detail.body") }}
              <code>{{ skill.body.path }}</code>
              <span v-if="skill.body.media_type">{{ skill.body.media_type }}</span>
              <span v-if="skill.body.size != null">
                {{ t("assembly.catalog.detail.size", { size: skill.body.size }) }}
              </span>
              <span v-if="skill.body.digest">
                {{ t("assembly.catalog.detail.digest", { digest: skill.body.digest }) }}
              </span>
            </p>
            <section v-for="group in attachmentGroups(skill)" :key="group.kind">
              <h4>{{ group.label }}</h4>
              <p v-if="!group.items.length" class="ability-detail-note">{{ group.empty }}</p>
              <ul v-else class="ability-members">
                <li v-for="item in group.items" :key="item.key">
                  <code>{{ item.key }}</code>
                  <code>{{ item.path }}</code>
                  <span v-if="item.media_type">{{ item.media_type }}</span>
                  <span v-if="item.size != null">
                    {{ t("assembly.catalog.detail.size", { size: item.size }) }}
                  </span>
                  <span v-if="item.digest">
                    {{ t("assembly.catalog.detail.digest", { digest: item.digest }) }}
                  </span>
                </li>
              </ul>
            </section>
          </li>
        </ul>
      </CollapsibleSection>

      <CollapsibleSection :title="t('assembly.catalog.detail.mcp')" default-open>
        <template v-if="profile.mcp">
          <p class="ability-detail-note">
            {{ t("assembly.catalog.detail.mcpServer", { server: profile.mcp.server }) }}
            {{ t("assembly.catalog.detail.mcpFetched", { time: profile.mcp.fetched_at }) }}
            {{
              profile.mcp.stale
                ? t("assembly.catalog.detail.mcpStale")
                : t("assembly.catalog.detail.mcpFresh")
            }}
          </p>
          <p v-if="profile.mcp.last_error" class="ability-detail-note">
            {{ t("assembly.catalog.detail.mcpLastError", { error: profile.mcp.last_error }) }}
          </p>
        </template>
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
      </CollapsibleSection>

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
  gap: var(--spacing-3);
}

.ability-detail-note,
.ability-facts,
.ability-members,
.ability-verify,
.ability-verify p,
.ability-detail-alert p {
  margin: 0;
}

.ability-detail-note,
.ability-facts,
.ability-verify {
  color: var(--color-text-muted);
  font-size: var(--font-ui-note);
  line-height: var(--leading-note);
}

.ability-facts {
  display: grid;
  grid-template-columns: repeat(auto-fit, minmax(12rem, 1fr));
  gap: var(--spacing-2) var(--spacing-3);
}

.ability-facts dt {
  color: var(--color-text-muted);
}

.ability-facts dd {
  margin: 0;
  color: var(--color-text);
  overflow-wrap: anywhere;
}

.ability-members,
.ability-verify ul {
  display: flex;
  min-width: 0;
  flex-direction: column;
  gap: var(--spacing-2);
  margin: 0;
  padding: 0;
  list-style: none;
}

.ability-members p {
  margin: 0;
}

.ability-members code,
.ability-verify code,
.ability-facts code {
  font-size: var(--font-ui-code);
}

.ability-detail h4 {
  margin: var(--spacing-2) 0 0;
  color: var(--color-text);
  font-size: var(--font-ui-note);
  font-weight: 600;
}

.ability-verify {
  display: flex;
  flex-direction: column;
  gap: var(--spacing-2);
}

.ability-detail-alert {
  display: flex;
  min-width: 0;
  flex-wrap: wrap;
  align-items: center;
  gap: var(--spacing-2) var(--spacing-3);
  color: var(--color-danger);
  font-size: var(--font-ui-note);
}
</style>
