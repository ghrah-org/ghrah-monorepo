// SPDX-FileCopyrightText: 2026 chenxya <chenxya@ghrah.org>
//
// SPDX-License-Identifier: Apache-2.0

import { defineConfig } from "vitest/config";

/**
 * 包级 vitest 配置：使 ``pnpm --filter @ghrah/observer-core test`` 与
 * ``pnpm -r test`` 可用（不依赖根 vitest.config.ts 的 packages/* glob）。
 */
export default defineConfig({
  test: {
    globals: true,
    environment: "node",
    include: ["src/**/*.spec.ts", "src/**/*.test.ts"],
  },
});
