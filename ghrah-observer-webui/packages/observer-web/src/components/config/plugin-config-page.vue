<!--
SPDX-FileCopyrightText: 2026 chenxya <chenxya@ghrah.org>

SPDX-License-Identifier: Apache-2.0
-->

<script setup lang="ts">
import { usePluginsStore } from "@ghrah/observer-core";
import { computed } from "vue";
import { useI18n } from "vue-i18n";
import { usePlugins } from "@/composables/usePlugins";

const plugins = usePluginsStore();
const { t } = useI18n();
const { manifestError, loadResults } = usePlugins();

const negotiation = computed(() => plugins.negotiation);
const hasAnyState = computed(
  () =>
    !!negotiation.value &&
    (negotiation.value.matched.length > 0 ||
      negotiation.value.python_only.length > 0 ||
      negotiation.value.ts_only.length > 0 ||
      negotiation.value.version_conflicts.length > 0 ||
      negotiation.value.missing_capabilities.length > 0),
);
const failedLoads = computed(() => loadResults.value.filter((item) => !item.ok));
</script>

<template>
  <div class="config-page plugin-config-page">
    <p v-if="manifestError" class="plugin-config-error" role="alert">
      {{ t("config.plugins.manifestError") }}
    </p>
    <p v-if="!negotiation" class="plugin-config-empty">{{ t("config.plugins.pending") }}</p>
    <p v-else-if="!hasAnyState" class="plugin-config-empty">{{ t("config.plugins.empty") }}</p>

    <section v-if="negotiation?.matched.length" class="plugin-config-section">
      <h2>{{ t("config.plugins.matched") }}</h2>
      <ul class="plugin-config-list">
        <li v-for="item in negotiation.matched" :key="item.plugin_id">
          <strong>{{ item.plugin_id }}</strong>
          <code>{{ item.version }}</code>
          <span v-if="item.instances.length" class="plugin-config-meta">
            {{ t("config.plugins.instances", item.instances.length) }}
          </span>
        </li>
      </ul>
    </section>

    <section v-if="negotiation?.version_conflicts.length" class="plugin-config-section">
      <h2>{{ t("config.plugins.versionConflicts") }}</h2>
      <ul class="plugin-config-list">
        <li v-for="item in negotiation.version_conflicts" :key="item.plugin_id">
          <strong>{{ item.plugin_id }}</strong>
          <span class="plugin-config-meta">
            {{ t("config.plugins.versions", { python: item.python_version, ts: item.ts_version }) }}
          </span>
        </li>
      </ul>
    </section>

    <section v-if="negotiation?.python_only.length" class="plugin-config-section">
      <h2>{{ t("config.plugins.pythonOnly") }}</h2>
      <ul class="plugin-config-list">
        <li v-for="item in negotiation.python_only" :key="item.plugin_id">
          <strong>{{ item.plugin_id }}</strong>
          <code>{{ item.version }}</code>
        </li>
      </ul>
    </section>

    <section v-if="negotiation?.ts_only.length" class="plugin-config-section">
      <h2>{{ t("config.plugins.tsOnly") }}</h2>
      <ul class="plugin-config-list">
        <li v-for="item in negotiation.ts_only" :key="item.plugin_id">
          <strong>{{ item.plugin_id }}</strong>
          <code>{{ item.version }}</code>
        </li>
      </ul>
    </section>

    <section v-if="negotiation?.missing_capabilities.length" class="plugin-config-section">
      <h2>{{ t("config.plugins.missingCapabilities") }}</h2>
      <ul class="plugin-config-list">
        <li v-for="item in negotiation.missing_capabilities" :key="item">
          <code>{{ item }}</code>
        </li>
      </ul>
    </section>

    <section v-if="plugins.crashed.length" class="plugin-config-section">
      <h2>{{ t("config.plugins.crashed") }}</h2>
      <ul class="plugin-config-list">
        <li v-for="(item, index) in plugins.crashed" :key="`${item.plugin_id}-${index}`">
          <strong>{{ item.plugin_id }}</strong>
          <span class="plugin-config-meta">{{ item.command }}: {{ item.error }}</span>
        </li>
      </ul>
    </section>

    <section v-if="failedLoads.length" class="plugin-config-section">
      <h2>{{ t("config.plugins.loadFailed") }}</h2>
      <ul class="plugin-config-list">
        <li v-for="item in failedLoads" :key="item.pluginId">
          <strong>{{ item.pluginId }}</strong>
          <span class="plugin-config-meta">{{ "error" in item ? item.error : "" }}</span>
        </li>
      </ul>
    </section>
  </div>
</template>
