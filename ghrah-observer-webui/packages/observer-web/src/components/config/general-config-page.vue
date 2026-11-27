<!--
SPDX-FileCopyrightText: 2026 chenxya <chenxya@ghrah.org>

SPDX-License-Identifier: Apache-2.0
-->

<script setup lang="ts">
import { useI18n } from "vue-i18n";
import UiSelect from "@/components/ui/select.vue";
import {
  FONT_SCALE_MAX,
  FONT_SCALE_MIN,
  FONT_SCALE_STEP,
  fontScale,
  fontScalePercent,
  setFontScale,
} from "@/composables/useFontScale";
import { roomAlign, setRoomAlign } from "@/composables/useRoomAlign";
import { type AppLocale, SUPPORTED_LOCALES, setLocale } from "@/i18n";

const { t, locale } = useI18n();

function onLocaleChange(event: Event) {
  setLocale((event.target as HTMLSelectElement).value as AppLocale);
}

function onAlignChange(event: Event) {
  setRoomAlign((event.target as HTMLSelectElement).value);
}

function onScaleInput(event: Event) {
  setFontScale((event.target as HTMLInputElement).value);
}
</script>

<template>
  <div class="flex flex-col h-full max-w-xl">
    <h2 class="text-lg font-semibold mb-4">{{ t("config.general.title") }}</h2>

    <section class="mb-6">
      <label for="general-locale" class="block text-sm font-medium mb-1">
        {{ t("config.general.language") }}
      </label>
      <p class="text-xs text-gray-500 dark:text-gray-400 mb-2">
        {{ t("config.general.languageDescription") }}
      </p>
      <UiSelect id="general-locale" :model-value="locale" @change="onLocaleChange">
        <option v-for="option in SUPPORTED_LOCALES" :key="option" :value="option">
          {{ t(`app.languageNames.${option}`) }}
        </option>
      </UiSelect>
    </section>

    <section class="mb-6">
      <label for="general-scale" class="block text-sm font-medium mb-1">
        {{ t("config.general.interfaceScale") }}
      </label>
      <p class="text-xs text-gray-500 dark:text-gray-400 mb-2">
        {{ t("config.general.interfaceScaleDescription") }}
      </p>
      <div class="flex items-center gap-3">
        <input
          id="general-scale"
          type="range"
          class="flex-1 accent-blue-600"
          :min="FONT_SCALE_MIN"
          :max="FONT_SCALE_MAX"
          :step="FONT_SCALE_STEP"
          :value="fontScale"
          @input="onScaleInput"
        />
        <span class="text-sm w-12 text-right tabular-nums">{{ fontScalePercent }}%</span>
      </div>
    </section>
    <section>
      <label for="general-room-align" class="block text-sm font-medium mb-1">{{
        t("config.general.roomAlign")
      }}</label>
      <p id="general-room-align-description" class="setting-description">
        {{ t("config.general.roomAlignDescription") }}
      </p>
      <UiSelect
        id="general-room-align"
        aria-describedby="general-room-align-description"
        :model-value="roomAlign"
        @change="onAlignChange"
      >
        <option value="self-right">{{ t("config.general.roomAlignOptions.selfRight") }}</option>
        <option value="left-all">{{ t("config.general.roomAlignOptions.leftAll") }}</option>
      </UiSelect>
    </section>
  </div>
</template>

<style scoped>
.setting-description {
  color: var(--color-text-muted);
  font-size: var(--font-ui-note);
  margin-block-end: var(--spacing-2);
}
</style>
