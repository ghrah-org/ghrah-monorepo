// SPDX-FileCopyrightText: 2026 chenxya <chenxya@ghrah.org>
//
// SPDX-License-Identifier: Apache-2.0

// ghrah 插件共享 Vue shim：import map 把裸说明符 "vue" 映射到本文件。
// 宿主 main.ts 在启动时挂载 globalThis.__GHRAH_VUE__（显式宿主装配行为）；
// 本文件按白名单 re-export 宿主 Vue 实例的 API，保证插件与宿主共享同一 Vue。
// 缺失宿主挂载时立即 throw（fail-fast）；新增 API 须显式扩白名单。
const Vue = globalThis.__GHRAH_VUE__;
if (!Vue) {
  throw new Error("[ghrah] plugins-shared/vue.js: host did not mount globalThis.__GHRAH_VUE__");
}

export const defineComponent = Vue.defineComponent;
export const h = Vue.h;
export const ref = Vue.ref;
export const computed = Vue.computed;
export const reactive = Vue.reactive;
export const readonly = Vue.readonly;
export const watch = Vue.watch;
export const watchEffect = Vue.watchEffect;
export const onMounted = Vue.onMounted;
export const onUnmounted = Vue.onUnmounted;
export const inject = Vue.inject;
export const provide = Vue.provide;
export const nextTick = Vue.nextTick;
export const Fragment = Vue.Fragment;
