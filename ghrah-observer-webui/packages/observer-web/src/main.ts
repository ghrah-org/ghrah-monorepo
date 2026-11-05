// SPDX-FileCopyrightText: 2026 chenxya <chenxya@ghrah.org>
//
// SPDX-License-Identifier: Apache-2.0

import { createPinia } from "pinia";
import * as Vue from "vue";
import { createApp } from "vue";
import "./styles/layers.css";
import "virtual:uno.css";
import "./styles/reset.css";
import "./styles/tokens.css";
import "./styles/base.css";
import "./style.css";
import App from "./app.vue";
import { initFontScale } from "./composables/useFontScale";
import { i18n } from "./i18n";

// 插件宿主装配：暴露宿主 Vue 实例（import map 的 "vue" shim 消费，单实例共享）。
(globalThis as { __GHRAH_VUE__?: typeof Vue }).__GHRAH_VUE__ = Vue;

function initDarkMode() {
  const stored = localStorage.getItem("theme");
  if (stored === "dark" || (!stored && window.matchMedia("(prefers-color-scheme: dark)").matches)) {
    document.documentElement.classList.add("dark");
  } else {
    document.documentElement.classList.remove("dark");
  }
}
initDarkMode();
initFontScale();

const app = createApp(App);
app.use(createPinia());
app.use(i18n);
app.mount("#app");
