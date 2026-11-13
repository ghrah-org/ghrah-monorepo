<!--
SPDX-FileCopyrightText: 2026 chenxya <chenxya@ghrah.org>

SPDX-License-Identifier: Apache-2.0
-->

<script setup lang="ts">
import { abilityPackageVersionKey } from "@ghrah/observer-core";
import type { AbilityPackageGroupVersionView } from "@ghrah/protocol";
import { useId } from "vue";
import { useI18n } from "vue-i18n";
import UiButton from "@/components/ui/button.vue";

const props = defineProps<{
  title: string;
  fullName: string | null;
  namespace: string | null;
  name: string | null;
  versions: AbilityPackageGroupVersionView[];
  expandedKeys: ReadonlySet<string>;
}>();

const emit = defineEmits<{
  toggle: [identity: { namespace: string; name: string; version: string }];
}>();

const { t } = useI18n();
const baseId = useId();

function originText(origin: "builtin" | "installed" | null): string {
  if (origin === "builtin") return t("assembly.catalog.origin.builtin");
  if (origin === "installed") return t("assembly.catalog.origin.installed");
  return t("assembly.catalog.origin.missing");
}

function problemText(problem: string): string {
  if (problem === "definition_missing") return t("assembly.versions.missingDefinition");
  if (problem.startsWith("definition_unreadable")) {
    return t("assembly.versions.unreadableDefinition");
  }
  return problem;
}

function identity(
  version: AbilityPackageGroupVersionView,
): { namespace: string; name: string; version: string } | null {
  if (!props.namespace || !props.name || !version.version) return null;
  return { namespace: props.namespace, name: props.name, version: version.version };
}

function versionKey(version: AbilityPackageGroupVersionView): string | null {
  const target = identity(version);
  if (!target) return null;
  return abilityPackageVersionKey(target.namespace, target.name, target.version);
}

function isExpanded(version: AbilityPackageGroupVersionView): boolean {
  const key = versionKey(version);
  return key !== null && props.expandedKeys.has(key);
}

function detailDomId(index: number): string {
  return `ability-version-${baseId}-${index}`;
}

function toggle(version: AbilityPackageGroupVersionView): void {
  const target = identity(version);
  if (target) emit("toggle", target);
}
</script>

<template>
  <li class="ability-group">
    <h3>{{ title }}</h3>
    <p v-if="fullName" class="ability-note">
      <code>{{ fullName }}</code>
    </p>
    <ul class="ability-versions">
      <li
        v-for="(version, index) in versions"
        :key="`${version.origin ?? ''}:${version.version ?? index}`"
        class="ability-version-row"
      >
        <div class="ability-version">
          <span>{{ version.version }}</span>
          <span>{{ originText(version.origin) }}</span>
          <span v-if="version.lock_state">
            {{ t("assembly.catalog.lock", { state: version.lock_state }) }}
          </span>
          <span v-if="version.member_count != null">
            {{ t("assembly.catalog.members", { count: version.member_count }) }}
          </span>
          <span v-if="version.shadowed_builtin">{{ t("assembly.catalog.shadowed") }}</span>
          <span v-if="version.problem">{{ problemText(version.problem) }}</span>
          <UiButton
            v-if="identity(version)"
            type="button"
            variant="ghost"
            size="sm"
            :aria-expanded="isExpanded(version)"
            :aria-controls="detailDomId(index)"
            @click="toggle(version)"
          >
            {{
              isExpanded(version)
                ? t("assembly.catalog.detail.collapse")
                : t("assembly.catalog.detail.expand")
            }}
          </UiButton>
        </div>
        <div v-if="isExpanded(version)" :id="detailDomId(index)" class="ability-version-detail">
          <slot name="detail" :version="version" />
        </div>
      </li>
    </ul>
  </li>
</template>

<style scoped>
.ability-group {
  min-width: 0;
  padding: var(--spacing-3) var(--spacing-4);
  border: 1px solid var(--color-border);
  border-radius: var(--radius-md);
  background: var(--color-surface);
}

.ability-group h3,
.ability-note {
  margin: 0;
}

.ability-group h3 {
  color: var(--color-text);
  font-size: var(--font-ui-body);
  font-weight: 600;
}

.ability-note {
  margin-top: var(--spacing-2);
  color: var(--color-text-muted);
  font-size: var(--font-ui-note);
  line-height: var(--leading-note);
}

.ability-group code {
  font-size: var(--font-ui-code);
}

.ability-versions {
  display: flex;
  min-width: 0;
  flex-direction: column;
  gap: var(--spacing-2);
  margin: var(--spacing-2) 0 0;
  padding: 0;
  list-style: none;
}

.ability-version {
  display: flex;
  min-width: 0;
  flex-wrap: wrap;
  align-items: center;
  gap: var(--spacing-2) var(--spacing-3);
  color: var(--color-text-muted);
  font-size: var(--font-ui-note);
}

.ability-version-detail {
  min-width: 0;
  margin-top: var(--spacing-2);
}
</style>
