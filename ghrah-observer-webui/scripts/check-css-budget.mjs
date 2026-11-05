// SPDX-FileCopyrightText: 2026 chenxya <chenxya@ghrah.org>
//
// SPDX-License-Identifier: Apache-2.0

// CSS 体积预算：构建产物只许变小，变大要显式确认。
//
// 读 packages/observer-web/dist/assets/*.css，统计原始字节与 gzip 字节，
// 与 style-budget.json 里的基线比较；超过基线 × (1 + tolerance) 即失败。
//
// 用法（先构建）：
//   pnpm --filter @ghrah/observer-web build
//   node scripts/check-css-budget.mjs
//   node scripts/check-css-budget.mjs --update          # 以当前产物重写基线
//   node scripts/check-css-budget.mjs --tolerance=0.05  # 临时放宽 5%
//
// 为什么用 gzip 作为主指标：仓库产物走 HTTP 压缩，用户实际传输的是 gzip 体积；
// 原始字节只作辅助，防止为了压 gzip 而写出难以阅读的代码。

import { readFileSync, readdirSync, writeFileSync } from "node:fs";
import { dirname, join, relative } from "node:path";
import { fileURLToPath } from "node:url";
import { gzipSync } from "node:zlib";

const WEBUI_ROOT = join(dirname(fileURLToPath(import.meta.url)), "..");
const DIST_ASSETS = join(WEBUI_ROOT, "packages", "observer-web", "dist", "assets");
const BUDGET_PATH = join(WEBUI_ROOT, "style-budget.json");

const update = process.argv.includes("--update");
const toleranceArg = process.argv.find((arg) => arg.startsWith("--tolerance="));
const tolerance = toleranceArg ? Number(toleranceArg.split("=")[1]) : undefined;

function fail(message) {
  console.error(message);
  process.exit(1);
}

let cssFiles;
try {
  cssFiles = readdirSync(DIST_ASSETS)
    .filter((file) => file.endsWith(".css"))
    .sort()
    .map((file) => join(DIST_ASSETS, file));
} catch {
  fail(
    `未找到构建产物：${relative(process.cwd(), DIST_ASSETS)}\n` +
      "先运行 pnpm --filter @ghrah/observer-web build，再执行本检查。",
  );
}

if (cssFiles.length === 0) fail(`${relative(process.cwd(), DIST_ASSETS)} 下没有 .css 产物。`);

const buffers = cssFiles.map((file) => readFileSync(file));
const rawBytes = buffers.reduce((sum, buffer) => sum + buffer.length, 0);
const gzipBytes = gzipSync(Buffer.concat(buffers), { level: 9 }).length;

const format = (bytes) => `${(bytes / 1024).toFixed(2)} kB (${bytes} B)`;

if (update) {
  const budget = {
    $comment:
      "CSS 体积基线：超过 baseline × (1 + tolerance) 即失败。重写用 node scripts/check-css-budget.mjs --update。",
    updated: new Date().toISOString().slice(0, 10),
    rawBytes,
    gzipBytes,
    tolerance: 0.02,
  };
  writeFileSync(BUDGET_PATH, `${JSON.stringify(budget, null, 2)}\n`);
  console.log(`已写入体积基线 ${relative(process.cwd(), BUDGET_PATH)}`);
  console.log(`  raw  ${format(rawBytes)}`);
  console.log(`  gzip ${format(gzipBytes)}`);
  process.exit(0);
}

let budget;
try {
  budget = JSON.parse(readFileSync(BUDGET_PATH, "utf8"));
} catch (error) {
  fail(
    `读取体积基线失败：${BUDGET_PATH}\n${error.message}\n` +
      "先运行 node scripts/check-css-budget.mjs --update 生成基线。",
  );
}

const allowed = tolerance ?? budget.tolerance ?? 0.02;
const limit = (bytes) => Math.floor(bytes * (1 + allowed));

const checks = [
  { name: "gzip", actual: gzipBytes, budget: budget.gzipBytes },
  { name: "raw ", actual: rawBytes, budget: budget.rawBytes },
];

console.log(
  `CSS 体积预算（允许上浮 ${(allowed * 100).toFixed(0)}%，产物 ${cssFiles.length} 个：${cssFiles
    .map((file) => relative(DIST_ASSETS, file))
    .join(", ")}）`,
);

let failed = false;
for (const { name, actual, budget: base } of checks) {
  const max = limit(base);
  const ratio = ((actual / base - 1) * 100).toFixed(1);
  const ok = actual <= max;
  console.log(
    `  ${ok ? "✓" : "✗"} ${name} ${format(actual)}  基线 ${format(base)}  上限 ${format(max)}  (${ratio}%)`,
  );
  if (!ok) failed = true;
}

if (failed) {
  console.error(
    "\nCSS 体积超出预算。先确认增量是否必要：" +
      "新增全局样式优先改为组件 scoped 或复用已有类；确有必要的，在评审说明后运行 --update。",
  );
  process.exit(1);
}

console.log("\nCSS 体积在预算内。");
