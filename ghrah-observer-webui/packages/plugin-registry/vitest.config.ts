// SPDX-FileCopyrightText: 2026 chenxya <chenxya@ghrah.org>
//
// SPDX-License-Identifier: Apache-2.0

import { defineConfig } from "vitest/config";

/**
 * 包级 vitest 配置：使 ``pnpm --filter @ghrah/plugin-registry test`` 与
 * ``pnpm -r test`` 递归形态可用（根 vitest.config.ts 单实例也覆盖本包）。
 */
export default defineConfig({
  test: {
    globals: true,
    environment: "node",
    include: ["src/**/*.spec.ts", "src/**/*.test.ts"],
  },
});
