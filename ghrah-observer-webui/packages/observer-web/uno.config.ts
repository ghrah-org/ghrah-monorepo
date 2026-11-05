// SPDX-FileCopyrightText: 2026 chenxya <chenxya@ghrah.org>
//
// SPDX-License-Identifier: Apache-2.0

import { defineConfig, presetIcons, presetUno } from "unocss";

export default defineConfig({
  darkMode: "class",
  /*
   * 把 UnoCSS 的内部层输出为 CSS 级联层，与 src/styles/layers.css 的声明对齐：
   * - preflights（--un-* 变量默认值）归入 base，和元素默认样式同层；
   * - 图标样式单独保留 icons 层（未在层顺序里声明，按首次出现追加到末尾）；
   * - 其余工具类统一归入 utilities，确保原子类可以覆盖 components 层。
   */
  outputToCssLayers: {
    cssLayerName: (layer) => {
      if (layer === "preflights") return "base";
      if (layer === "icons") return "icons";
      return "utilities";
    },
  },
  presets: [
    presetUno(),
    presetIcons({
      scale: 1.2,
      cdn: "https://esm.sh/",
    }),
  ],
  shortcuts: {
    btn: "px-3 py-1.5 rounded border-none cursor-pointer text-sm font-medium transition-colors duration-200",
    "btn-primary":
      "btn bg-blue-600 text-white hover:bg-blue-700 disabled:opacity-50 disabled:cursor-not-allowed",
    "btn-danger":
      "btn bg-red-600 text-white hover:bg-red-700 disabled:opacity-50 disabled:cursor-not-allowed",
    "btn-secondary":
      "btn bg-gray-200 text-gray-800 hover:bg-gray-300 dark:bg-gray-700 dark:text-gray-200 dark:hover:bg-gray-600 disabled:opacity-50 disabled:cursor-not-allowed",
    panel: "bg-white dark:bg-gray-900 border border-gray-200 dark:border-gray-700 rounded",
    badge: "inline-block px-2 py-0.5 rounded-full text-xs font-medium",
  },
  theme: {
    colors: {
      primary: "#0066cc",
    },
    fontSize: {
      xs: "0.8125rem",
      sm: "0.9375rem",
      base: "1rem",
      lg: "1.125rem",
    },
  },
});
