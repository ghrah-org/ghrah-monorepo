<!--
SPDX-FileCopyrightText: 2026 chenxya <chenxya@ghrah.org>

SPDX-License-Identifier: Apache-2.0
-->

<script setup lang="ts">
import { abilityPackageVersionKey } from "@ghrah/observer-core";
import type { AbilityPackageGroupVersionView } from "@ghrah/protocol";
import { computed, ref, useId } from "vue";
import { useI18n } from "vue-i18n";

const props = defineProps<{
  title: string;
  fullName: string | null;
  namespace: string | null;
  name: string | null;
  versions: AbilityPackageGroupVersionView[];
  expandedKeys: ReadonlySet<string>;
  /** 由版本画像算出的工具/技能拆分；契约还没有这两个字段时由上层补。 */
  counts?: { tools: number; skills: number } | null;
}>();

const emit = defineEmits<{
  toggle: [identity: { namespace: string; name: string; version: string }];
}>();

const { t } = useI18n();
const baseId = useId();

/** 多版本时才需要组级展开；单元包直接由包的展开态驱动。 */
const groupOpen = ref(false);

const primary = computed<AbilityPackageGroupVersionView | undefined>(() => props.versions[0]);
const multi = computed(() => props.versions.length > 1);

// 退化身份（只有 namespace）在服务端以 <namespace>/<namespace> 落盘，请求路径同样要补 name。
const storageName = computed(() => props.name ?? props.namespace);

const origin = computed<"builtin" | "installed" | null>(() => primary.value?.origin ?? null);

const problem = computed<string | null>(() => {
  const hit = props.versions.find((version) => version.problem);
  return hit?.problem ?? null;
});

/**
 * 读工具/技能计数。
 *
 * 这两个字段还没有进入契约（``AbilityPackageGroupVersionView`` 目前只有 ``member_count``），
 * 但显示规则已经定了：有值就按“工具 · 技能”展示，缺值回退成员数。
 * 这里只读可选字段，字段一到位就自动生效，不需要再改这一处。
 */
function readCount(version: AbilityPackageGroupVersionView, key: string): number | null {
  const raw = version as unknown as Record<string, unknown>;
  const value = raw[key];
  return typeof value === "number" ? value : null;
}

const counts = computed<{ tools: number; skills: number } | null>(() => {
  if (props.counts) return props.counts;
  const version = primary.value;
  if (!version) return null;
  // 目录接口直接带成员键：优先用它，不再逐版本读画像（老载荷可能没有这两列）。
  const operations = version.operations ?? [];
  const skills = version.skills ?? [];
  if (operations.length > 0 || skills.length > 0) {
    return { tools: operations.length, skills: skills.length };
  }
  const tools = readCount(version, "operation_count");
  const skillsCount = readCount(version, "skill_count");
  if (tools === null || skillsCount === null) return null;
  return { tools, skills: skillsCount };
});

/** 单元包显示组合计数；多版本包显示版本数，避免把某一行的事实说成整包事实。 */
const metaLabel = computed(() => {
  if (multi.value) return t("assembly.catalog.versionCount", { count: props.versions.length });
  const split = counts.value;
  if (split) {
    const parts: string[] = [];
    if (split.tools > 0) {
      parts.push(t("assembly.catalog.toolCount", { count: split.tools }));
    }
    if (split.skills > 0) {
      parts.push(t("assembly.catalog.skillCount", { count: split.skills }));
    }
    if (parts.length === 0) parts.push(t("assembly.catalog.toolCount", { count: 0 }));
    return parts.join(" · ");
  }
  const count = primary.value?.member_count;
  if (count == null) return "";
  return t("assembly.catalog.members", { count });
});

const originLabel = computed(() => {
  if (origin.value === "builtin") return t("assembly.catalog.origin.builtin");
  if (origin.value === "installed") return t("assembly.catalog.origin.installed");
  return t("assembly.catalog.origin.missing");
});

const open = computed(() =>
  multi.value ? groupOpen.value : primary.value != null && isExpanded(primary.value),
);

const bodyId = `${baseId}-body`;

/** 图标兜底：取名称首字母，色相由包全名散列得到；同一包始终同色。 */
const tileStyle = computed(() => {
  const seed = props.fullName ?? props.title;
  let hue = 0;
  for (let index = 0; index < seed.length; index += 1) {
    hue = (hue * 31 + seed.charCodeAt(index)) % 360;
  }
  return `--pkg-hue: ${hue}`;
});

const monogram = computed(() => {
  const words = props.title.split(/[\s_-]+/).filter(Boolean);
  if (words.length > 1) return (words[0][0] + words[1][0]).toUpperCase();
  return props.title.slice(0, 1).toUpperCase();
});

function versionKey(version: AbilityPackageGroupVersionView): string | null {
  if (!props.namespace || !storageName.value || !version.version) return null;
  return abilityPackageVersionKey(props.namespace, storageName.value, version.version);
}

function isExpanded(version: AbilityPackageGroupVersionView): boolean {
  const key = versionKey(version);
  return key !== null && props.expandedKeys.has(key);
}

function versionOriginLabel(version: AbilityPackageGroupVersionView): string {
  if (version.origin === "builtin") return t("assembly.catalog.origin.builtin");
  if (version.origin === "installed") return t("assembly.catalog.origin.installed");
  return t("assembly.catalog.origin.missing");
}

function versionBodyId(index: number): string {
  return `${baseId}-version-${index}`;
}

function emitToggle(version: AbilityPackageGroupVersionView): void {
  if (!props.namespace || !storageName.value || !version.version) return;
  emit("toggle", {
    namespace: props.namespace,
    name: storageName.value,
    version: version.version,
  });
}

function onHead(): void {
  if (multi.value) {
    groupOpen.value = !groupOpen.value;
    return;
  }
  if (primary.value) emitToggle(primary.value);
}
</script>

<template>
  <li class="ability-package" :data-open="open">
    <button
      type="button"
      class="ability-package-head"
      :aria-expanded="open"
      :aria-controls="bodyId"
      @click="onHead"
    >
      <span class="ability-package-tile" :style="tileStyle" aria-hidden="true">
        {{ monogram }}
      </span>
      <span class="ability-package-id">
        <span class="ability-package-titleline">
          <span class="ability-package-title">{{ title }}</span>
          <span class="ability-pill">{{ originLabel }}</span>
          <span v-if="problem" class="ability-pill ability-pill-warn">
            {{ t("assembly.catalog.attention") }}
          </span>
        </span>
        <span class="ability-package-sub">
          <code v-if="fullName">{{ fullName }}</code>
          <span v-if="fullName && primary?.version && primary.origin === 'installed'" class="ability-package-sep">·</span>
          <!-- 内核件随源码发布，没有版本概念：只在能力件上显示版本号。 -->
          <span v-if="primary?.version && primary.origin === 'installed'">v{{ primary.version }}</span>
        </span>
      </span>
      <span class="ability-package-meta">
        <span v-if="metaLabel">{{ metaLabel }}</span>
        <svg
          class="ability-package-chevron"
          width="12"
          height="12"
          viewBox="0 0 12 12"
          fill="none"
          stroke="currentColor"
          stroke-width="1.6"
          stroke-linecap="round"
          stroke-linejoin="round"
          aria-hidden="true"
        >
          <path d="M3 4.5 6 7.5 9 4.5" />
        </svg>
      </span>
    </button>

    <div v-if="open" :id="bodyId" class="ability-package-body">
      <template v-if="!multi && primary">
        <slot name="detail" :version="primary" />
      </template>
      <ul v-else class="ability-version-list">
        <li
          v-for="(version, index) in versions"
          :key="`${version.origin ?? ''}:${version.version ?? index}`"
          class="ability-version"
        >
          <button
            type="button"
            class="ability-version-head"
            :aria-expanded="isExpanded(version)"
            :aria-controls="versionBodyId(index)"
            @click="emitToggle(version)"
          >
            <span v-if="version.origin === 'installed'" class="ability-version-name">
              v{{ version.version }}
            </span>
            <span class="ability-pill">{{ versionOriginLabel(version) }}</span>
            <span v-if="version.problem" class="ability-pill ability-pill-warn">
              {{ t("assembly.catalog.attention") }}
            </span>
            <span v-if="version.member_count != null" class="ability-version-meta">
              {{ t("assembly.catalog.members", { count: version.member_count }) }}
            </span>
            <svg
              class="ability-package-chevron"
              width="12"
              height="12"
              viewBox="0 0 12 12"
              fill="none"
              stroke="currentColor"
              stroke-width="1.6"
              stroke-linecap="round"
              stroke-linejoin="round"
              aria-hidden="true"
            >
              <path d="M3 4.5 6 7.5 9 4.5" />
            </svg>
          </button>
          <div v-if="isExpanded(version)" :id="versionBodyId(index)" class="ability-version-body">
            <slot name="detail" :version="version" />
          </div>
        </li>
      </ul>
    </div>
  </li>
</template>

<style scoped>
.ability-package {
  min-width: 0;
  border: 1px solid var(--color-border);
  border-radius: var(--radius-lg);
  background: var(--color-surface);
}

.ability-package[data-open="true"] {
  border-color: var(--color-border-strong);
}

.ability-package-head {
  display: grid;
  grid-template-columns: auto minmax(0, 1fr) auto;
  align-items: center;
  gap: 0.75rem;
  width: 100%;
  padding: 0.625rem 0.75rem;
  border: none;
  border-radius: var(--radius-lg);
  background: transparent;
  color: inherit;
  text-align: left;
  cursor: pointer;
}

.ability-package-head:hover {
  background: var(--color-surface-2);
  border-radius: var(--radius-lg);
}

.ability-package-tile {
  display: grid;
  place-items: center;
  width: 2.125rem;
  height: 2.125rem;
  border: 1px solid color-mix(in srgb, hsl(var(--pkg-hue) 45% 52%) 32%, var(--color-border));
  border-radius: 0.4375rem;
  background: color-mix(in srgb, hsl(var(--pkg-hue) 45% 52%) 15%, var(--color-surface-2));
  color: var(--color-text);
  font-size: 0.75rem;
  font-weight: 700;
  letter-spacing: 0.01em;
}

.ability-package-id {
  display: flex;
  min-width: 0;
  flex-direction: column;
  gap: 0.125rem;
}

.ability-package-titleline {
  display: flex;
  min-width: 0;
  flex-wrap: wrap;
  align-items: center;
  gap: 0.4375rem;
}

.ability-package-title {
  overflow: hidden;
  color: var(--color-text);
  font-size: 0.9375rem;
  font-weight: 600;
  letter-spacing: -0.005em;
  text-overflow: ellipsis;
  white-space: nowrap;
}

.ability-package-sub {
  display: flex;
  min-width: 0;
  align-items: center;
  gap: 0.375rem;
  color: var(--color-text-subtle);
  font-size: 0.75rem;
}

.ability-package-sub code {
  overflow: hidden;
  color: var(--color-text-muted);
  font-size: var(--font-ui-code);
  text-overflow: ellipsis;
  white-space: nowrap;
}

.ability-package-sep {
  opacity: 0.6;
}

.ability-pill {
  display: inline-flex;
  flex: none;
  align-items: center;
  gap: 0.25rem;
  height: 1.125rem;
  padding: 0 0.375rem;
  border: 1px solid transparent;
  border-radius: 999px;
  background: var(--color-surface-3);
  color: var(--color-text-muted);
  font-size: 0.6875rem;
  font-weight: 600;
  line-height: var(--leading-tight);
  white-space: nowrap;
}

.ability-pill-warn {
  border-color: color-mix(in srgb, var(--color-warn) 45%, transparent);
  background: color-mix(in srgb, var(--color-warn) 16%, var(--color-surface));
  color: var(--color-warn);
}

.ability-package-meta {
  display: flex;
  align-items: center;
  gap: 0.75rem;
  color: var(--color-text-subtle);
  font-size: 0.75rem;
  white-space: nowrap;
}

.ability-package-chevron {
  flex: none;
  transition: transform 160ms ease;
}

.ability-package[data-open="true"] > .ability-package-head .ability-package-chevron {
  transform: rotate(180deg);
}

.ability-package-body {
  display: flex;
  min-width: 0;
  flex-direction: column;
  gap: var(--spacing-2);
  padding: 0.875rem 0.875rem 1rem;
  border-top: 1px solid var(--color-border);
}

.ability-version-list {
  display: flex;
  min-width: 0;
  flex-direction: column;
  gap: var(--spacing-2);
  margin: 0;
  padding: 0;
  list-style: none;
}

.ability-version {
  min-width: 0;
}

.ability-version-head {
  display: flex;
  min-width: 0;
  flex-wrap: wrap;
  align-items: center;
  gap: var(--spacing-2);
  width: 100%;
  padding: var(--spacing-2);
  border: 1px solid var(--color-border);
  border-radius: var(--radius-md);
  background: var(--color-surface-2);
  color: inherit;
  font-size: var(--font-ui-note);
  text-align: left;
  cursor: pointer;
}

.ability-version-head:hover {
  border-color: var(--color-border-strong);
}

.ability-version-head[aria-expanded="true"] .ability-package-chevron {
  transform: rotate(180deg);
}

.ability-version-name {
  color: var(--color-text);
  font-weight: 600;
}

.ability-version-meta {
  margin-left: auto;
  color: var(--color-text-subtle);
  font-size: var(--font-ui-eyebrow);
}

.ability-version-body {
  min-width: 0;
  padding: var(--spacing-2) var(--spacing-2) 0;
}
</style>
