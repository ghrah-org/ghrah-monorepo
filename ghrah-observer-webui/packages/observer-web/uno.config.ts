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
    btn: "inline-flex items-center justify-center rounded border-none cursor-pointer font-medium leading-none whitespace-nowrap transition-colors duration-200",
    "btn-md": "btn h-8 px-3 text-xs leading-none",
    "btn-sm": "btn h-7 px-2 text-xs leading-none",
    "btn-xs": "btn h-[1.375rem] px-2 text-xs leading-none",
    "btn-primary":
      "bg-[var(--color-action)] text-[color:var(--color-on-action)] hover:bg-[color-mix(in_srgb,var(--color-action)_88%,var(--color-on-action))] disabled:opacity-50 disabled:cursor-not-allowed",
    "btn-danger":
      "bg-[color-mix(in_srgb,var(--color-danger)_12%,var(--color-surface))] text-[color:var(--color-danger)] hover:bg-[color-mix(in_srgb,var(--color-danger)_20%,var(--color-surface))] disabled:opacity-50 disabled:cursor-not-allowed",
    "btn-secondary":
      "bg-[var(--color-surface-2)] text-[color:var(--color-text)] hover:bg-[var(--color-surface-3)] disabled:opacity-50 disabled:cursor-not-allowed",
    "btn-ghost":
      "bg-transparent text-[color:var(--color-text)] hover:bg-[var(--color-surface-3)] disabled:opacity-50 disabled:cursor-not-allowed",
    "icon-button":
      "inline-flex shrink-0 items-center justify-center rounded border-none p-0 cursor-pointer text-base leading-none transition-colors duration-200",
    "icon-button-xs": "icon-button h-[1.375rem] w-[1.375rem]",
    "icon-button-sm": "icon-button h-7 w-7",
    "icon-button-md": "icon-button h-8 w-8",
    "icon-button-ghost":
      "bg-transparent text-[color:var(--color-text-muted)] hover:bg-[var(--color-surface-3)] hover:text-[color:var(--color-text)]",
    "icon-button-solid":
      "bg-[var(--color-surface-2)] text-[color:var(--color-text-muted)] hover:bg-[var(--color-surface-3)] hover:text-[color:var(--color-text)]",
    field:
      "box-border rounded border border-[color:var(--color-border-strong)] bg-[var(--color-surface-2)] text-sm focus:outline-none focus:ring-2 focus:ring-[color:var(--color-selected-line)]",
    "field-input": "field h-8 w-full px-2",
    "field-multiline": "field min-h-8 w-full px-2",
    "field-select": "field h-8 w-full appearance-none pl-2 pr-8",
    "field-select-wrap": "relative block min-w-0",
    "field-chevron":
      "pointer-events-none absolute right-2 top-1/2 h-2.5 w-2.5 -translate-y-1/2 text-[color:var(--color-text-muted)]",
    "field-reject": "focus:outline-none focus:ring-1 focus:ring-[color:var(--color-danger)]",
    panel: "bg-[var(--color-surface)] border border-[color:var(--color-border)] rounded",
    "ui-badge":
      "inline-flex items-center h-5 px-2 rounded-full text-[0.6875rem] font-medium leading-none",
    "ui-badge-neutral": "ui-badge bg-[var(--color-surface-2)] text-[color:var(--color-text-muted)]",
    "ui-badge-accent": "ui-badge bg-[var(--color-surface-3)] text-[color:var(--color-text)]",
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
