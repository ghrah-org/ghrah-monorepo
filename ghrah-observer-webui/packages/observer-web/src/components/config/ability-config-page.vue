<!--
SPDX-FileCopyrightText: 2026 chenxya <chenxya@ghrah.org>

SPDX-License-Identifier: Apache-2.0
-->

<script setup lang="ts">
import { abilityPackageVersionKey, useAbilityPackagesStore } from "@ghrah/observer-core";
import { computed, onMounted, onUnmounted, ref } from "vue";
import { useI18n } from "vue-i18n";
import AbilityPackageCard from "@/components/config/ability-package-card.vue";
import AbilityPackageDetail from "@/components/config/ability-package-detail.vue";
import UiButton from "@/components/ui/button.vue";

defineEmits<{ dirtyChange: [dirty: boolean] }>();
const packages = useAbilityPackagesStore();
const { t } = useI18n();
const expanded = ref(new Set<string>());

const legacy = computed(
  () => packages.status?.legacy_deprecated === true || packages.status?.ability_source === "legacy",
);
const pending = computed(() => packages.loadedAt === null && !packages.failed);
const statusLabel = computed(() => {
  if (!packages.status) {
    return packages.failed ? t("assembly.statusUnavailable") : t("assembly.loading");
  }
  if (legacy.value) return t("assembly.statusLegacy");
  if (packages.status.features.tree) return t("assembly.statusBuiltin");
  return t("assembly.statusUnavailable");
});
const showEmpty = computed(() => {
  if (pending.value || packages.loading || packages.groupsFailed || packages.failed) return false;
  return !packages.groups || packages.groups.groups.length === 0;
});

function groupKey(
  fullName: string | null,
  versions: { version: string | null; origin: string | null }[],
): string {
  if (fullName) return fullName;
  return versions.map((version) => `${version.origin ?? ""}:${version.version ?? ""}`).join("|");
}

function versionIdentity(key: string): { namespace: string; name: string; version: string } | null {
  const at = key.lastIndexOf("@");
  if (at <= 0) return null;
  const fullName = key.slice(0, at);
  const version = key.slice(at + 1);
  const dot = fullName.indexOf(".");
  if (dot <= 0 || !version) return null;
  return {
    namespace: fullName.slice(0, dot),
    name: fullName.slice(dot + 1),
    version,
  };
}

function listedVersionKeys(): Set<string> {
  const keys = new Set<string>();
  for (const group of packages.groups?.groups ?? []) {
    if (!group.namespace || !group.name) continue;
    for (const version of group.versions) {
      if (!version.version) continue;
      keys.add(abilityPackageVersionKey(group.namespace, group.name, version.version));
    }
  }
  return keys;
}

function onToggle(identity: { namespace: string; name: string; version: string }): void {
  const key = abilityPackageVersionKey(identity.namespace, identity.name, identity.version);
  const next = new Set(expanded.value);
  if (next.delete(key)) {
    expanded.value = next;
    return;
  }
  next.add(key);
  expanded.value = next;
  const record = packages.versionProfile(identity.namespace, identity.name, identity.version);
  const cachedAt = record?.loadedAt;
  if (record?.loading || (cachedAt !== null && cachedAt !== undefined)) return;
  void packages.loadVersionProfile(identity.namespace, identity.name, identity.version);
}

function onRetry(namespace: string, name: string, version: string): void {
  void packages.loadVersionProfile(namespace, name, version);
}

async function refresh(): Promise<void> {
  const open = expanded.value;
  await packages.load();
  if (packages.failed) return;
  const live = packages.groups ? listedVersionKeys() : null;
  const next = new Set<string>();
  for (const key of open) {
    if (live && !live.has(key)) continue;
    const identity = versionIdentity(key);
    if (!identity) continue;
    next.add(key);
    void packages.loadVersionProfile(identity.namespace, identity.name, identity.version);
  }
  expanded.value = next;
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
      <UiButton type="button" variant="secondary" @click="refresh()">
        {{ t("assembly.catalog.refresh") }}
      </UiButton>
    </header>
    <p v-if="packages.status?.reason" class="ability-note">
      {{ t("assembly.reason", { reason: packages.status.reason }) }}
    </p>
    <p v-if="pending || packages.loading" class="ability-state">{{ t("assembly.loading") }}</p>
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
      <AbilityPackageCard
        v-for="group in packages.groups.groups"
        :key="groupKey(group.full_name, group.versions)"
        :title="group.title || group.full_name || t('assembly.versions.unreadable')"
        :full-name="group.full_name"
        :namespace="group.namespace"
        :name="group.name"
        :versions="group.versions"
        :expanded-keys="expanded"
        @toggle="onToggle"
      >
        <template #detail="{ version }">
          <AbilityPackageDetail
            v-if="group.namespace && group.name && version.version"
            :record="packages.versionProfile(group.namespace, group.name, version.version)"
            :mcp-probe-enabled="packages.status?.features.mcp_probe === true"
            @retry="onRetry(group.namespace, group.name, version.version)"
          />
        </template>
      </AbilityPackageCard>
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

.ability-groups {
  display: flex;
  min-width: 0;
  flex-direction: column;
  gap: var(--spacing-3);
  margin: 0;
  padding: 0;
  list-style: none;
}
</style>
