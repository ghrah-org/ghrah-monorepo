// SPDX-FileCopyrightText: 2026 chenxya <chenxya@ghrah.org>
//
// SPDX-License-Identifier: Apache-2.0

import { createI18n } from "vue-i18n";
import en from "./locales/en";
import zhCN from "./locales/zh-CN";

export const SUPPORTED_LOCALES = ["en", "zh-CN"] as const;
export type AppLocale = (typeof SUPPORTED_LOCALES)[number];
export type MessageSchema = typeof en;

const STORAGE_KEY = "ghrah-locale";

export function normalizeLocale(value: string | null | undefined): AppLocale {
  return value?.toLowerCase().startsWith("zh") ? "zh-CN" : "en";
}

export function getInitialLocale(): AppLocale {
  const stored = typeof window === "undefined" ? null : window.localStorage.getItem(STORAGE_KEY);
  const browser = typeof navigator === "undefined" ? null : navigator.language;
  return normalizeLocale(stored ?? browser);
}

export function syncDocumentLocale(locale: AppLocale) {
  if (typeof document !== "undefined") {
    document.documentElement.lang = locale;
  }
}

export const i18n = createI18n({
  fallbackLocale: "en",
  legacy: false,
  locale: getInitialLocale(),
  messages: {
    en,
    "zh-CN": zhCN,
  },
});

syncDocumentLocale(i18n.global.locale.value as AppLocale);

export function setLocale(locale: AppLocale) {
  i18n.global.locale.value = locale;
  if (typeof window !== "undefined") {
    window.localStorage.setItem(STORAGE_KEY, locale);
  }
  syncDocumentLocale(locale);
}
