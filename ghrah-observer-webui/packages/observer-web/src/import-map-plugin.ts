// SPDX-FileCopyrightText: 2026 chenxya <chenxya@ghrah.org>
//
// SPDX-License-Identifier: Apache-2.0

import { readFileSync } from "node:fs";
import { dirname, resolve } from "node:path";
import { fileURLToPath } from "node:url";
import type { Plugin } from "vite";

/**
 * import map 注入插件（D5）：读宿主装配产物 `public/plugins/import-map.json`，
 * `imports` 非空时向 `<head>` 注入 `<script type="importmap">`。
 * dev 与 build 同一逻辑（build 产物 `dist/index.html` 自带，无需服务端模板）。
 * 文件缺失/`imports` 空 → 不注入（零插件零注入，零隐式）。
 * 仅在 Vite 配置期运行于 Node，不进入浏览器产物。
 */
export interface ImportMapPluginOptions {
  /** 测试用：覆盖 import-map.json 路径。 */
  importMapPath?: string;
}

export function importMapPlugin(options: ImportMapPluginOptions = {}): Plugin {
  // 本文件位于 src/；产物在包根 public/plugins/。
  const importMapPath =
    options.importMapPath ??
    resolve(dirname(fileURLToPath(import.meta.url)), "../public/plugins/import-map.json");

  function readImports(): Record<string, string> {
    try {
      const raw = JSON.parse(readFileSync(importMapPath, "utf8")) as {
        imports?: Record<string, string>;
      };
      return raw.imports ?? {};
    } catch {
      return {};
    }
  }

  return {
    name: "ghrah-import-map",
    transformIndexHtml(html) {
      const imports = readImports();
      if (Object.keys(imports).length === 0) return html;
      const json = JSON.stringify({ imports }).replace(/</g, "\\u003c");
      return html.replace("</head>", `  <script type="importmap">${json}</script>\n  </head>`);
    },
  };
}
