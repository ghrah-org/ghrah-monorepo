// SPDX-FileCopyrightText: 2026 chenxya <chenxya@ghrah.org>
//
// SPDX-License-Identifier: Apache-2.0

/// <reference types="vite/client" />
/// <reference types="unocss/vite" />

declare module "*.vue" {
  import type { DefineComponent } from "vue";

  const component: DefineComponent<object, object, unknown>;
  export default component;
}

/** 插件宿主装配挂载（main.ts）：import map 的 "vue" shim 读取。 */
declare global {
  var __GHRAH_VUE__: typeof import("vue");
}
