// SPDX-FileCopyrightText: 2026 chenxya <chenxya@ghrah.org>
//
// SPDX-License-Identifier: Apache-2.0

import { defineConfig } from "tsup";

export default defineConfig({
  entry: ["src/index.ts"],
  format: ["esm"],
  dts: {
    resolve: true,
    compilerOptions: {
      composite: false,
    },
  },
  clean: true,
  sourcemap: true,
  esbuildOptions(options) {
    options.sourcesContent = false;
  },
});
