<!--
SPDX-FileCopyrightText: 2026 chenxya <chenxya@ghrah.org>

SPDX-License-Identifier: Apache-2.0
-->

<script setup lang="ts">
import { useI18n } from "vue-i18n";
import BaseModal from "./base-modal.vue";
import UiButton from "@/components/ui/button.vue";

withDefaults(
  defineProps<{
    title: string;
    message: string;
    confirmLabel?: string;
    cancelLabel?: string;
    danger?: boolean;
  }>(),
  { confirmLabel: undefined, cancelLabel: undefined, danger: false },
);
defineEmits<{ confirm: []; cancel: [] }>();
const { t } = useI18n();
</script>

<template>
  <BaseModal :title="title" size="sm" :show-close="false" @request-close="$emit('cancel')">
    <p class="confirm-dialog-message">{{ message }}</p>
    <template #footer>
      <UiButton type="button" variant="secondary" data-autofocus @click="$emit('cancel')">
        {{ cancelLabel ?? t("common.cancel") }}
      </UiButton>
      <UiButton type="button" :variant="danger ? 'danger' : 'primary'" @click="$emit('confirm')">
        {{ confirmLabel ?? t("common.confirm") }}
      </UiButton>
    </template>
  </BaseModal>
</template>
