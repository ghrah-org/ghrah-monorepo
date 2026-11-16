<!--
SPDX-FileCopyrightText: 2026 chenxya <chenxya@ghrah.org>

SPDX-License-Identifier: Apache-2.0
-->

<script setup lang="ts">
import { abilityPackageVersionKey, useAbilityPackagesStore } from "@ghrah/observer-core";
import type { AbilityPackageGroupView } from "@ghrah/protocol";
import { computed, onMounted, onUnmounted, ref, watch } from "vue";
import { useI18n } from "vue-i18n";
import AbilityPackageCard from "@/components/config/ability-package-card.vue";
import AbilityPackageDetail from "@/components/config/ability-package-detail.vue";
import AbilityPackageImportDialog from "@/components/config/ability-package-import-dialog.vue";
import UiButton from "@/components/ui/button.vue";

defineEmits<{ dirtyChange: [dirty: boolean] }>();

const packages = useAbilityPackagesStore();
const { t } = useI18n();

type OriginFilter = "all" | "builtin" | "installed" | "attention";
type SortKey = "name" | "members" | "origin";

const query = ref("");
const filter = ref<OriginFilter>("all");
const sort = ref<SortKey>("name");
const grouped = ref(true);
const collapsed = ref(new Set<string>());
const expanded = ref(new Set<string>());
const importOpen = ref(false);
const installedNotice = ref<string | null>(null);
const importMaxBytes = 256 << 20;

const canInstall = computed(() => packages.status?.features.packages_install === true);

const pending = computed(() => packages.loadedAt === null && !packages.failed);
const legacy = computed(
  () => packages.status?.legacy_deprecated === true || packages.status?.ability_source === "legacy",
);
const catalogHidden = computed(
  () => packages.status !== null && packages.status.features.packages_read === false,
);
const ready = computed(
  () => packages.status !== null && packages.status.features.tree === true && !legacy.value,
);

const allGroups = computed<AbilityPackageGroupView[]>(() => packages.groups?.groups ?? []);

const statusTitle = computed(() => {
  if (!packages.status) {
    return packages.failed ? t("assembly.statusUnavailable") : t("assembly.loading");
  }
  if (legacy.value) return t("assembly.statusLegacy");
  if (packages.status.features.tree) return t("assembly.statusBuiltin");
  return t("assembly.statusUnavailable");
});

const counts = computed(() => {
  let builtin = 0;
  let installed = 0;
  let attention = 0;
  let members = 0;
  for (const group of allGroups.value) {
    const primary = group.versions[0];
    if (primary?.origin === "builtin") builtin += 1;
    else if (primary?.origin === "installed") installed += 1;
    if (group.versions.some((version) => version.problem)) attention += 1;
    members += primary?.member_count ?? 0;
  }
  return { builtin, installed, attention, members, total: allGroups.value.length };
});

const overviewSub = computed(() =>
  t("assembly.catalog.ui.overview", {
    packages: counts.value.total,
    members: counts.value.members,
  }),
);

const notices = computed<string[]>(() => {
  const lines: string[] = [];
  if (packages.status?.reason) lines.push(t("assembly.reason", { reason: packages.status.reason }));
  if (legacy.value) lines.push(t("assembly.catalog.legacy"));
  if (catalogHidden.value) lines.push(t("assembly.catalog.packagesHidden"));
  if (packages.status && !packages.status.features.tree && !legacy.value) {
    lines.push(t("assembly.catalog.unavailable"));
  }
  return lines;
});

const alertLine = computed(() => {
  if (packages.failed) return t("assembly.loadFailed");
  if (packages.groupsFailed) return t("assembly.catalog.groupsFailed");
  return null;
});

const hasCatalog = computed(
  () => !catalogHidden.value && !legacy.value && (packages.groups?.groups.length ?? 0) > 0,
);

function originOf(group: AbilityPackageGroupView): "builtin" | "installed" | null {
  return group.versions[0]?.origin ?? null;
}

function searchBlob(group: AbilityPackageGroupView): string {
  return [group.title ?? "", group.full_name ?? "", group.namespace ?? "", group.name ?? ""]
    .join(" ")
    .toLowerCase();
}

function matches(group: AbilityPackageGroupView): boolean {
  const needle = query.value.trim().toLowerCase();
  if (needle && !searchBlob(group).includes(needle)) return false;
  if (filter.value === "builtin") return originOf(group) === "builtin";
  if (filter.value === "installed") return originOf(group) === "installed";
  if (filter.value === "attention") return group.versions.some((version) => version.problem);
  return true;
}

function memberCountOf(group: AbilityPackageGroupView): number {
  return group.versions[0]?.member_count ?? 0;
}

const visibleGroups = computed<AbilityPackageGroupView[]>(() => {
  const rows = allGroups.value.filter(matches);
  const byName = (a: AbilityPackageGroupView, b: AbilityPackageGroupView): number =>
    (a.title ?? a.full_name ?? "").localeCompare(b.title ?? b.full_name ?? "");
  if (sort.value === "members") {
    return [...rows].sort((a, b) => memberCountOf(b) - memberCountOf(a) || byName(a, b));
  }
  if (sort.value === "origin") {
    return [...rows].sort((a, b) => {
      const left = originOf(a) ?? "zzz";
      const right = originOf(b) ?? "zzz";
      return left === right ? byName(a, b) : left.localeCompare(right);
    });
  }
  return [...rows].sort(byName);
});

interface Section {
  key: string;
  label: string;
  groups: AbilityPackageGroupView[];
}

const sections = computed<Section[]>(() => {
  if (!grouped.value) {
    return [{ key: "all", label: "", groups: visibleGroups.value }];
  }
  const buckets: Section[] = [
    {
      key: "installed",
      label: t("assembly.catalog.origin.installed"),
      groups: visibleGroups.value.filter((group) => originOf(group) === "installed"),
    },
    {
      key: "builtin",
      label: t("assembly.catalog.origin.builtin"),
      groups: visibleGroups.value.filter((group) => originOf(group) === "builtin"),
    },
    {
      key: "missing",
      label: t("assembly.catalog.origin.missing"),
      groups: visibleGroups.value.filter((group) => originOf(group) === null),
    },
  ];
  return buckets.filter((bucket) => bucket.groups.length > 0);
});

const showEmpty = computed(
  () => !pending.value && !packages.loading && hasCatalog.value && visibleGroups.value.length === 0,
);

const showNoCatalog = computed(
  () =>
    !pending.value &&
    !packages.loading &&
    !hasCatalog.value &&
    !legacy.value &&
    !catalogHidden.value,
);

function groupName(group: AbilityPackageGroupView): string {
  return group.name ?? group.namespace ?? "";
}

function cardKey(group: AbilityPackageGroupView): string {
  return group.full_name ?? `${group.namespace ?? ""}:${group.versions[0]?.version ?? ""}`;
}

function toggleSection(key: string): void {
  const next = new Set(collapsed.value);
  if (next.has(key)) next.delete(key);
  else next.add(key);
  collapsed.value = next;
}

function versionIdentity(key: string): { namespace: string; name: string; version: string } | null {
  const at = key.lastIndexOf("@");
  if (at <= 0) return null;
  const fullName = key.slice(0, at);
  const version = key.slice(at + 1);
  const dot = fullName.indexOf(".");
  if (dot <= 0 || !version) return null;
  return { namespace: fullName.slice(0, dot), name: fullName.slice(dot + 1), version };
}

/**
 * 行内的「工具 · 技能」拆分还没有契约字段，用已有的版本画像补出来。
 *
 * 只对当前可见的行预取，并且有上限：包一多就退回成员数，不为了一个角标打出上百个请求。
 * 契约将来带上 ``operation_count`` / ``skill_count`` 时，卡片会优先用那两个字段。
 */
const PREFETCH_LIMIT = 40;
const profileInflight = new Set<string>();

/** 三个入口（预取、展开、刷新）共用同一个去重集合，避免同一版本被排两次队。 */
function ensureProfile(namespace: string, name: string, version: string): void {
  const key = abilityPackageVersionKey(namespace, name, version);
  if (profileInflight.has(key)) return;
  const record = packages.versionProfile(namespace, name, version);
  if (record?.loading || record?.loadedAt != null) return;
  profileInflight.add(key);
  void packages
    .loadVersionProfile(namespace, name, version)
    .finally(() => profileInflight.delete(key));
}

function profileCounts(group: AbilityPackageGroupView): { tools: number; skills: number } | null {
  const name = groupName(group);
  const version = group.versions[0];
  if (!group.namespace || !name || !version?.version) return null;
  const profile = packages.versionProfile(group.namespace, name, version.version)?.profile;
  if (!profile) return null;
  return { tools: profile.operations.length, skills: profile.skills.length };
}

function prefetchCounts(rows: AbilityPackageGroupView[]): void {
  let budget = PREFETCH_LIMIT;
  for (const group of rows) {
    if (budget <= 0) break;
    const name = groupName(group);
    const version = group.versions[0];
    if (!group.namespace || !name || !version?.version) continue;
    ensureProfile(group.namespace, name, version.version);
    budget -= 1;
  }
}

watch(visibleGroups, (rows) => prefetchCounts(rows), { immediate: true });

function listedVersionKeys(): Set<string> {
  const keys = new Set<string>();
  for (const group of allGroups.value) {
    const name = groupName(group);
    if (!group.namespace || !name) continue;
    for (const version of group.versions) {
      if (!version.version) continue;
      keys.add(abilityPackageVersionKey(group.namespace, name, version.version));
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
  ensureProfile(identity.namespace, identity.name, identity.version);
}

function onRetry(namespace: string, name: string, version: string): void {
  ensureProfile(namespace, name, version);
}

function resetFilters(): void {
  query.value = "";
  filter.value = "all";
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
    ensureProfile(identity.namespace, identity.name, identity.version);
  }
  expanded.value = next;
}

function onInstalled(view: {
  full_name: string;
  version: string;
  already_installed: boolean;
}): void {
  importOpen.value = false;
  installedNotice.value = t(
    view.already_installed
      ? "assembly.catalog.import.already"
      : "assembly.catalog.import.installed",
    { fullName: view.full_name, version: view.version },
  );
}

onMounted(() => packages.setSettingsVisible(true));
onUnmounted(() => packages.setSettingsVisible(false));
</script>

<template>
  <section class="ability-catalog">
    <div class="ability-overview">
      <div class="ability-overview-main">
        <span class="ability-overview-dot" :class="{ 'is-ready': ready }" aria-hidden="true" />
        <div>
          <p class="ability-overview-title">{{ statusTitle }}</p>
          <p class="ability-overview-sub">{{ overviewSub }}</p>
        </div>
      </div>
      <div class="ability-overview-side">
        <span class="ability-stat">
          {{ t("assembly.catalog.origin.builtin") }}
          <b>{{ counts.builtin }}</b>
        </span>
        <span class="ability-stat">
          {{ t("assembly.catalog.origin.installed") }}
          <b>{{ counts.installed }}</b>
        </span>
        <span v-if="counts.attention" class="ability-stat ability-stat-warn">
          {{ t("assembly.catalog.attention") }}
          <b>{{ counts.attention }}</b>
        </span>
        <UiButton variant="secondary" size="sm" @click="refresh()">
          {{ t("assembly.catalog.refresh") }}
        </UiButton>
        <UiButton
          variant="primary"
          size="sm"
          :disabled="!canInstall"
          :title="canInstall ? '' : t('assembly.catalog.installUnavailable')"
          @click="importOpen = true"
        >
          {{ t("assembly.catalog.install") }}
        </UiButton>
      </div>
    </div>

    <p v-for="line in notices" :key="line" class="ability-notice">{{ line }}</p>
    <p v-if="alertLine" class="ability-notice ability-notice-alert" role="alert">{{ alertLine }}</p>
    <p v-if="installedNotice" class="ability-notice">{{ installedNotice }}</p>
    <p v-if="pending || packages.loading" class="ability-notice">{{ t("assembly.loading") }}</p>

    <div v-if="hasCatalog" class="ability-toolbar">
      <input
        v-model="query"
        type="search"
        class="ability-search field-input"
        :placeholder="t('assembly.catalog.ui.searchPlaceholder')"
        :aria-label="t('assembly.catalog.ui.searchPlaceholder')"
      />
      <div class="ability-filter" role="group" :aria-label="t('assembly.catalog.ui.filter')">
        <button
          v-for="option in ['all', 'builtin', 'installed', 'attention'] as const"
          :key="option"
          type="button"
          class="ability-filter-button"
          :aria-pressed="filter === option"
          @click="filter = option"
        >
          {{ t(`assembly.catalog.ui.filterOptions.${option}`) }}
        </button>
      </div>
      <div
        class="ability-filter ability-view"
        role="group"
        :aria-label="t('assembly.catalog.ui.view')"
      >
        <button
          type="button"
          class="ability-filter-button"
          :aria-pressed="grouped"
          @click="grouped = true"
        >
          {{ t("assembly.catalog.ui.grouped") }}
        </button>
        <button
          type="button"
          class="ability-filter-button"
          :aria-pressed="!grouped"
          @click="grouped = false"
        >
          {{ t("assembly.catalog.ui.flat") }}
        </button>
      </div>
    </div>

    <div v-if="hasCatalog" class="ability-listhead">
      <h2>{{ t("assembly.catalog.ui.count", { count: visibleGroups.length }) }}</h2>
      <div class="ability-sort">
        <span>{{ t("assembly.catalog.ui.sort") }}</span>
        <button
          v-for="option in ['name', 'members', 'origin'] as const"
          :key="option"
          type="button"
          class="ability-sort-button"
          :aria-pressed="sort === option"
          @click="sort = option"
        >
          {{ t(`assembly.catalog.ui.sortOptions.${option}`) }}
        </button>
      </div>
    </div>

    <template v-if="hasCatalog && visibleGroups.length">
      <section v-for="section in sections" :key="section.key" class="ability-section">
        <button
          v-if="section.label"
          type="button"
          class="ability-section-head"
          :aria-expanded="!collapsed.has(section.key)"
          @click="toggleSection(section.key)"
        >
          <svg
            class="ability-section-chevron"
            width="11"
            height="11"
            viewBox="0 0 12 12"
            fill="none"
            stroke="currentColor"
            stroke-width="1.7"
            stroke-linecap="round"
            stroke-linejoin="round"
            aria-hidden="true"
          >
            <path d="M3 4.5 6 7.5 9 4.5" />
          </svg>
          {{ section.label }}
          <span class="ability-section-count">{{ section.groups.length }}</span>
        </button>
        <ul v-if="!collapsed.has(section.key)" class="ability-groups">
          <AbilityPackageCard
            v-for="group in section.groups"
            :key="cardKey(group)"
            :title="group.title || group.full_name || t('assembly.versions.unreadable')"
            :full-name="group.full_name"
            :namespace="group.namespace"
            :name="groupName(group)"
            :versions="group.versions"
            :expanded-keys="expanded"
            :counts="profileCounts(group)"
            @toggle="onToggle"
          >
            <template #detail="{ version }">
              <AbilityPackageDetail
                v-if="group.namespace && groupName(group) && version.version"
                :record="
                  packages.versionProfile(group.namespace, groupName(group), version.version)
                "
                :mcp-probe-enabled="packages.status?.features.mcp_probe === true"
                @retry="onRetry(group.namespace, groupName(group), version.version)"
              />
            </template>
          </AbilityPackageCard>
        </ul>
      </section>
    </template>

    <div v-else-if="showEmpty" class="ability-empty">
      <p>{{ t("assembly.catalog.ui.noMatches") }}</p>
      <UiButton variant="secondary" size="sm" @click="resetFilters()">
        {{ t("assembly.catalog.ui.resetFilters") }}
      </UiButton>
    </div>

    <p v-else-if="showNoCatalog" class="ability-notice">{{ t("assembly.catalog.empty") }}</p>

    <AbilityPackageImportDialog
      :open="importOpen"
      :max-bytes="importMaxBytes"
      @close="importOpen = false"
      @installed="onInstalled"
    />
  </section>
</template>

<style scoped>
.ability-catalog {
  display: flex;
  width: 100%;
  max-width: 62rem;
  min-width: 0;
  flex-direction: column;
  gap: 0.875rem;
  margin-inline: auto;
  color: var(--color-text);
  font-size: var(--font-ui-body);
  overflow-wrap: anywhere;
}

.ability-overview {
  display: flex;
  flex-wrap: wrap;
  align-items: center;
  justify-content: space-between;
  gap: 0.875rem 1.25rem;
  padding: 0.8125rem 0.875rem 0.8125rem 1rem;
  border: 1px solid var(--color-border);
  border-radius: var(--radius-lg);
  background: var(--color-surface);
}

.ability-overview-main {
  display: flex;
  min-width: 0;
  align-items: center;
  gap: var(--spacing-2);
}

.ability-overview-dot {
  flex: none;
  width: 0.5rem;
  height: 0.5rem;
  border-radius: 999px;
  background: var(--color-text-subtle);
}

.ability-overview-dot.is-ready {
  background: var(--color-success);
}

.ability-overview-title {
  margin: 0;
  font-size: var(--font-ui-body);
  font-weight: 600;
}

.ability-overview-sub {
  margin: 0;
  color: var(--color-text-muted);
  font-size: var(--font-ui-note);
}

.ability-overview-side {
  display: flex;
  flex-wrap: wrap;
  align-items: center;
  gap: var(--spacing-2);
}

.ability-stat {
  display: inline-flex;
  align-items: baseline;
  gap: 0.3125rem;
  padding: 0.1875rem 0.5rem;
  border: 1px solid var(--color-border);
  border-radius: var(--radius-md);
  background: var(--color-surface-2);
  color: var(--color-text-muted);
  font-size: var(--font-ui-note);
}

.ability-stat b {
  color: var(--color-text);
  font-variant-numeric: tabular-nums;
}

.ability-stat-warn {
  border-color: color-mix(in srgb, var(--color-warn) 45%, transparent);
  background: color-mix(in srgb, var(--color-warn) 12%, var(--color-surface));
  color: var(--color-warn);
}

.ability-stat-warn b {
  color: var(--color-warn);
}

.ability-notice {
  margin: 0;
  padding: var(--spacing-2) var(--spacing-3);
  border: 1px solid var(--color-border);
  border-radius: var(--radius-md);
  background: var(--color-surface-2);
  color: var(--color-text-muted);
  font-size: var(--font-ui-note);
  line-height: var(--leading-note);
}

.ability-notice-alert {
  border-color: var(--color-danger);
  background: color-mix(in srgb, var(--color-danger) 12%, var(--color-surface));
  color: var(--color-danger);
}

.ability-toolbar {
  display: flex;
  flex-wrap: wrap;
  align-items: center;
  gap: var(--spacing-2);
}

.ability-search {
  flex: 1 1 12rem;
  max-width: 20rem;
  min-width: 10rem;
}

.ability-filter {
  display: inline-flex;
  gap: 0.125rem;
  padding: 0.125rem;
  border: 1px solid var(--color-border);
  border-radius: var(--radius-md);
  background: var(--color-surface-2);
}

.ability-view {
  margin-inline-start: auto;
}

.ability-filter-button {
  padding: 0.125rem 0.5rem;
  border: none;
  border-radius: 0.25rem;
  background: transparent;
  color: var(--color-text-muted);
  font-size: var(--font-ui-note);
  cursor: pointer;
}

.ability-filter-button:hover {
  color: var(--color-text);
}

.ability-filter-button[aria-pressed="true"] {
  background: var(--color-surface);
  color: var(--color-text);
  font-weight: 600;
}

.ability-listhead {
  display: flex;
  flex-wrap: wrap;
  align-items: center;
  justify-content: space-between;
  gap: var(--spacing-2);
}

.ability-listhead h2 {
  margin: 0;
  color: var(--color-text-muted);
  font-size: var(--font-ui-note);
  font-weight: 600;
}

.ability-sort {
  display: inline-flex;
  align-items: center;
  gap: 0.25rem;
  color: var(--color-text-subtle);
  font-size: var(--font-ui-eyebrow);
}

.ability-sort-button {
  padding: 0 0.3125rem;
  border: none;
  border-radius: 0.25rem;
  background: transparent;
  color: var(--color-text-muted);
  font-size: var(--font-ui-eyebrow);
  cursor: pointer;
}

.ability-sort-button[aria-pressed="true"] {
  background: var(--color-surface-3);
  color: var(--color-text);
  font-weight: 600;
}

.ability-section {
  display: flex;
  min-width: 0;
  flex-direction: column;
  gap: var(--spacing-2);
}

.ability-section-head {
  display: flex;
  align-items: center;
  gap: 0.375rem;
  margin-top: 0.5rem;
  padding: 0.375rem 0.25rem;
  border: none;
  border-radius: var(--radius-md);
  background: transparent;
  color: var(--color-text-muted);
  font-size: var(--font-ui-eyebrow);
  font-weight: 600;
  text-align: left;
  cursor: pointer;
}

.ability-section-chevron {
  flex: none;
  transition: transform 160ms ease;
}

.ability-section-head[aria-expanded="false"] .ability-section-chevron {
  transform: rotate(-90deg);
}

.ability-section-count {
  color: var(--color-text-subtle);
  font-variant-numeric: tabular-nums;
  font-weight: 500;
}

.ability-groups {
  display: flex;
  min-width: 0;
  flex-direction: column;
  gap: var(--spacing-2);
  margin: 0;
  padding: 0;
  list-style: none;
}

.ability-empty {
  display: flex;
  flex-direction: column;
  align-items: center;
  gap: var(--spacing-2);
  padding: var(--spacing-4);
  border: 1px dashed var(--color-border-strong);
  border-radius: var(--radius-md);
  color: var(--color-text-muted);
  font-size: var(--font-ui-note);
  text-align: center;
}

.ability-empty p {
  margin: 0;
}
</style>
