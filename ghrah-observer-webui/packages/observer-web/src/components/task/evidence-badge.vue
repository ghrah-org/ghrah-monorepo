<!--
SPDX-FileCopyrightText: 2026 chenxya <chenxya@ghrah.org>

SPDX-License-Identifier: Apache-2.0
-->

<script setup lang="ts">
import type { TaskEvidencePayload } from "@ghrah/protocol";
import { computed, type Component } from "vue";
import { usePlugins } from "@/composables/usePlugins";

const props = defineProps<{ evidence: TaskEvidencePayload }>();
const { resolveBadge } = usePlugins();

const badgeComponent = computed<Component | null>(() => resolveBadge(props.evidence.kind));
</script>

<template>
  <span class="evidence-badge">
    <component :is="badgeComponent" v-if="badgeComponent" :evidence="evidence" />
    <!-- raw 回退：未命中注册表/降级时按 kind + ref 字符串显示 -->
    <span v-else class="evidence-badge-raw">
      <code>{{ evidence.kind }}</code>
      <span class="evidence-badge-ref">{{ evidence.ref }}</span>
    </span>
  </span>
</template>
