// SPDX-FileCopyrightText: 2026 chenxya <chenxya@ghrah.org>
//
// SPDX-License-Identifier: Apache-2.0

// ESLint：承担 oxlint 覆盖不了的部分 —— Vue SFC/template 规则 + TS 项目规则。
// eslint-plugin-oxlint 依据 .oxlintrc.json 关闭与 oxlint 重复的规则（oxlint 先跑，见根 package.json lint 链）。
import eslint from "@eslint/js";
import { existsSync } from "node:fs";
import { dirname, join } from "node:path";
import { fileURLToPath } from "node:url";
import globals from "globals";
import oxlintPlugin from "eslint-plugin-oxlint";
import pluginVue from "eslint-plugin-vue";
import tseslint from "typescript-eslint";

export default tseslint.config(
  {
    ignores: [
      "**/node_modules/",
      "**/dist/",
      "**/dist-ssr/",
      "**/coverage/",
      "**/test-results/",
      "**/playwright-report/",
      // 宿主装配生成物（eslint 不读 .gitignore，须显式忽略）
      "**/public/plugins/",
    ],
  },
  eslint.configs.recommended,
  ...tseslint.configs.recommended,
  ...pluginVue.configs["flat/recommended"],
  {
    files: ["**/*.vue"],
    languageOptions: {
      parserOptions: { parser: tseslint.parser },
    },
  },
  {
    languageOptions: {
      globals: { ...globals.browser, ...globals.es2021, ...globals.node },
    },
  },
  // v-html 已由 useMarkdown 收口（html:false + 链接白名单），不新开渲染路径
  { rules: { "vue/no-v-html": "off" } },
  // prettier 负责格式：关闭与其冲突的格式类规则
  {
    rules: {
      "vue/max-attributes-per-line": "off",
      "vue/singleline-html-element-content-newline": "off",
      "vue/multiline-html-element-content-newline": "off",
      "vue/html-self-closing": "off",
      "vue/html-indent": "off",
      "vue/html-closing-bracket-newline": "off",
    },
  },
  {
    rules: {
      // 测试夹具中的 any 断言为存量模式（biome recommended 未含此规则），降为 warn 观察期
      "@typescript-eslint/no-explicit-any": "warn",
      // _ 前缀 = 有意忽略的参数/变量（仓内既有惯例）
      "@typescript-eslint/no-unused-vars": [
        "error",
        { argsIgnorePattern: "^_", varsIgnorePattern: "^_", caughtErrors: "none" },
      ],
    },
  },
  {
    files: ["**/pages/*.vue"],
    rules: {
      // 路由页按路由名命名（dashboard 等单词名为存量事实）
      "vue/multi-word-component-names": "off",
    },
  },
  // 在所有预设之后加载：按 .oxlintrc.json 关闭已被 oxlint 检查的重复规则。
  // 从本配置文件所在目录显式定位（子包目录运行 eslint 时 cwd 不同，需向上找配置根）。
  (() => {
    let dir = dirname(fileURLToPath(import.meta.url));
    for (let i = 0; i < 5; i++) {
      const candidate = join(dir, ".oxlintrc.json");
      if (existsSync(candidate)) return oxlintPlugin.buildFromOxlintConfigFile(candidate);
      dir = dirname(dir);
    }
    return {};
  })(),
);
