// SPDX-FileCopyrightText: 2026 chenxya <chenxya@ghrah.org>
//
// SPDX-License-Identifier: Apache-2.0

import { fileURLToPath, URL } from "node:url";
import vue from "@vitejs/plugin-vue";
import { defineConfig } from "vitest/config";

export default defineConfig({
  plugins: [vue()],
  resolve: {
    alias: {
      "@": fileURLToPath(new URL("./packages/observer-web/src", import.meta.url)),
    },
  },
  test: {
    globals: true,
    environment: "node",
    include: ["packages/*/src/**/*.spec.ts", "packages/*/src/**/*.test.ts"],
    setupFiles: ["packages/observer-web/src/test/setup.ts"],
    coverage: {
      provider: "v8",
      reporter: ["text", "json", "html"],
      include: ["packages/*/src/**/*.ts"],
      exclude: ["**/*.d.ts", "**/*.spec.ts", "**/*.test.ts"],
    },
  },
});
