// SPDX-FileCopyrightText: 2026 chenxya <chenxya@ghrah.org>
//
// SPDX-License-Identifier: Apache-2.0

import { fileURLToPath, URL } from "node:url";
import vue from "@vitejs/plugin-vue";
import { defineConfig } from "vitest/config";

/**
 * 包级 vitest 配置：使 ``pnpm --filter @ghrah/observer-web test`` 与
 * ``pnpm -r test`` 可用。复用根配置的 vue 插件 + ``@`` 别名（指向本包 src）。
 *
 * 组件测试用 happy-dom 环境（见各 *.spec.ts 顶部 ``@vitest-environment``）。
 */
export default defineConfig({
  plugins: [vue()],
  resolve: {
    alias: {
      "@": fileURLToPath(new URL("./src", import.meta.url)),
    },
  },
  test: {
    globals: true,
    environment: "node",
    include: ["src/**/*.spec.ts", "src/**/*.test.ts"],
    setupFiles: ["src/test/setup.ts"],
  },
});
