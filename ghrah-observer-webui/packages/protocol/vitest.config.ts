// SPDX-FileCopyrightText: 2026 chenxya <chenxya@ghrah.org>
//
// SPDX-License-Identifier: Apache-2.0

import { defineConfig } from "vitest/config";

/**
 * 包级 vitest 配置：使 ``pnpm --filter @ghrah/protocol test`` 与
 * ``pnpm -r test`` 递归形态可用（不依赖根 vitest.config.ts 的 packages/*
 * glob——包 cwd 下该 glob 解析为空，导致递归中断）。
 *
 * 根 ``pnpm test`` 仍走根 vitest.config.ts 单实例跨包匹配，二者不冲突。
 */
export default defineConfig({
  test: {
    globals: true,
    environment: "node",
    include: ["src/**/*.spec.ts", "src/**/*.test.ts"],
  },
});
