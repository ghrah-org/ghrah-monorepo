<!--
SPDX-FileCopyrightText: 2026 chenxya <chenxya@ghrah.org>

SPDX-License-Identifier: Apache-2.0
-->

<script setup lang="ts">
import { useAbilityPackagesStore } from "@ghrah/observer-core";
import { computed, onMounted, onUnmounted } from "vue";
import { useI18n } from "vue-i18n";
import UiButton from "@/components/ui/button.vue";

defineEmits<{ dirtyChange: [dirty: boolean] }>();
const packages = useAbilityPackagesStore();
const { t } = useI18n();

const legacy = computed(
  () => packages.status?.legacy_deprecated === true || packages.status?.ability_source === "legacy",
);
const statusLabel = computed(() => {
  if (!packages.status) return t("assembly.statusUnavailable");
  if (legacy.value) return t("assembly.statusLegacy");
  if (packages.status.features.tree) return t("assembly.statusBuiltin");
  return t("assembly.statusUnavailable");
});
const showEmpty = computed(() => {
  if (packages.loading || packages.groupsFailed) return false;
  return !packages.groups || packages.groups.groups.length === 0;
});

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

function groupKey(
  fullName: string | null,
  versions: { version: string | null; origin: string | null }[],
): string {
  if (fullName) return fullName;
  return versions.map((version) => `${version.origin ?? ""}:${version.version ?? ""}`).join("|");
}

onMounted(() => packages.setSettingsVisible(true));
onUnmounted(() => packages.setSettingsVisible(false));
</script>

<template>
  <section class="ability-catalog">
    <header class="ability-catalog-header">
      <div class="ability-catalog-heading">
        <h2>{{ t("assembly.catalog.title") }}</h2>
        <p>{{ statusLabel }}</p>
      </div>
      <UiButton type="button" variant="secondary" @click="packages.load()">
        {{ t("assembly.catalog.refresh") }}
      </UiButton>
    </header>
    <p v-if="packages.status?.reason" class="ability-note">
      {{ t("assembly.reason", { reason: packages.status.reason }) }}
    </p>
    <p v-if="packages.loading" class="ability-state">{{ t("assembly.loading") }}</p>
    <p v-if="packages.failed" class="ability-state ability-state-alert" role="alert">
      {{ t("assembly.loadFailed") }}
    </p>
    <p v-if="legacy" class="ability-state">{{ t("assembly.catalog.legacy") }}</p>
    <p v-else-if="packages.status && !packages.status.features.packages_read" class="ability-state">
      {{ t("assembly.catalog.packagesHidden") }}
    </p>
    <p v-if="packages.status && !packages.status.features.tree && !legacy" class="ability-state">
      {{ t("assembly.catalog.unavailable") }}
    </p>
    <p v-if="packages.status && !packages.status.features.packages_install" class="ability-state">
      {{ t("assembly.catalog.installHidden") }}
    </p>
    <p v-if="packages.groupsFailed" class="ability-state ability-state-alert" role="alert">
      {{ t("assembly.catalog.groupsFailed") }}
    </p>
    <ul v-if="packages.groups?.groups.length" class="ability-groups">
      <li
        v-for="group in packages.groups.groups"
        :key="groupKey(group.full_name, group.versions)"
        class="ability-group"
      >
        <h3>{{ group.title || group.full_name || t("assembly.versions.unreadable") }}</h3>
        <p v-if="group.full_name" class="ability-note">
          <code>{{ group.full_name }}</code>
        </p>
        <ul class="ability-versions">
          <li
            v-for="(version, index) in group.versions"
            :key="`${version.origin ?? ''}:${version.version ?? index}`"
            class="ability-version"
          >
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
          </li>
        </ul>
      </li>
    </ul>
    <p v-if="showEmpty" class="ability-state ability-state-empty">
      {{ t("assembly.catalog.empty") }}
    </p>
  </section>
</template>

<style scoped>
.ability-catalog {
  display: flex;
  max-width: 46rem;
  min-width: 0;
  flex-direction: column;
  gap: 0.75rem;
  color: var(--color-text);
  font-size: var(--font-ui-body);
  overflow-wrap: anywhere;
}

.ability-catalog-header {
  display: flex;
  flex-wrap: wrap;
  align-items: flex-start;
  justify-content: space-between;
  gap: 0.75rem 1rem;
}

.ability-catalog-heading h2,
.ability-catalog-heading p,
.ability-note,
.ability-state {
  margin: 0;
}

.ability-catalog-heading h2 {
  font-size: var(--font-ui-body);
  font-weight: 600;
}

.ability-catalog-heading p {
  margin-top: 0.25rem;
}

.ability-catalog-heading p,
.ability-note {
  color: var(--color-text-muted);
  font-size: var(--font-ui-note);
  line-height: 1.5;
}

.ability-state {
  padding: 0.75rem 1rem;
  border: 1px solid var(--color-border);
  border-radius: var(--radius-md);
  background: var(--color-surface-2);
  color: var(--color-text-muted);
  font-size: var(--font-ui-note);
  line-height: 1.5;
}

.ability-state-alert {
  border-color: var(--color-danger);
  background: color-mix(in srgb, var(--color-danger) 12%, var(--color-surface));
  color: var(--color-danger);
}

.ability-state-empty {
  background: var(--color-surface);
  text-align: center;
}

.ability-groups,
.ability-versions {
  display: flex;
  min-width: 0;
  flex-direction: column;
  gap: 0.75rem;
  margin: 0;
  padding: 0;
  list-style: none;
}

.ability-group {
  min-width: 0;
  padding: 0.75rem 1rem;
  border: 1px solid var(--color-border);
  border-radius: var(--radius-md);
  background: var(--color-surface);
}

.ability-group h3 {
  margin: 0;
  color: var(--color-text);
  font-size: var(--font-ui-body);
  font-weight: 600;
}

.ability-versions {
  gap: 0.5rem;
  margin-top: 0.5rem;
}

.ability-version {
  display: flex;
  min-width: 0;
  flex-wrap: wrap;
  gap: 0.25rem 0.75rem;
  color: var(--color-text-muted);
  font-size: var(--font-ui-note);
}

.ability-group code {
  font-size: var(--font-ui-code);
}
</style>
