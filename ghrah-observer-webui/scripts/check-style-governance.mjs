// 样式治理检查（ratchet 基线）。
//
// 只做一件事：把当前代码里的“已知违规”计数与 style-baseline.json 比较，
// 数量增加就失败、减少就提示下调基线。这样既能给新代码立规矩，又不会
// 因为存量代码一次性被卡死。
//
// 用法：
//   node scripts/check-style-governance.mjs            # 校验
//   node scripts/check-style-governance.mjs --update   # 以当前计数重写基线
//
// 规则定义见下方 RULES；每条规则都只统计“可执行、可复核”的文本模式：
//   color-no-hex       CSS/SFC <style> 里的裸十六进制色值（tokens.css 除外）
//   px-font-size       font-size 使用 px
//   important          !important
//   dark-variant       模板里的 dark: 变体
//   scoped-root-dark   .vue <style> 里的 :root.dark 覆盖
//   arbitrary-utility  模板里的任意值工具类（w-[...] 等）
//   palette-utility    模板里的调色板工具类（bg-gray-800、text-blue-500 等）
//
// 已知取舍：
// - 注释先剔除，避免文档示例被计数；
// - tokens.css 是唯一允许裸色值的文件，整文件豁免 color-no-hex；
// - 统计的是“出现次数”而不是“唯一值个数”，迁移时删一处就降一处，行为可预期。

import { readFileSync, readdirSync, statSync, writeFileSync } from "node:fs";
import { dirname, join, relative, sep } from "node:path";
import { fileURLToPath } from "node:url";

const WEBUI_ROOT = join(dirname(fileURLToPath(import.meta.url)), "..");
const BASELINE_PATH = join(WEBUI_ROOT, "style-baseline.json");
const TOKEN_FILE = join("packages", "observer-web", "src", "styles", "tokens.css");

const PALETTE =
  "(?:gray|slate|zinc|neutral|stone|red|orange|amber|yellow|lime|green|emerald|teal|cyan|sky|blue|indigo|violet|purple|fuchsia|pink|rose)";
const PALETTE_UTILITY = new RegExp(
  `\\b(?:bg|text|border|from|to|via|ring|fill|stroke)-${PALETTE}-(?:50|[1-9]00|950)\\b`,
  "g",
);

const RULES = {
  "color-no-hex": {
    description: "裸十六进制色值",
    scan: (text, file) => {
      if (file.endsWith(TOKEN_FILE)) return 0;
      return (text.match(/#[0-9a-fA-F]{3,8}\b/g) ?? []).length;
    },
  },
  "px-font-size": {
    description: "font-size 使用 px",
    // 只认“值直接以 px 数字开头”，calc(16px * var(--font-scale)) 这类合法写法不算
    scan: (text) => (text.match(/font-size\s*:\s*\d+(?:\.\d+)?px/g) ?? []).length,
  },
  important: {
    description: "!important",
    scan: (text) => (text.match(/!important/g) ?? []).length,
  },
  "dark-variant": {
    description: "模板 dark: 变体",
    scope: "template",
    scan: (text) => (text.match(/\bdark:[a-z0-9-]+/g) ?? []).length,
  },
  "scoped-root-dark": {
    description: "SFC <style> 中的 :root.dark",
    scope: "style",
    scan: (text) => (text.match(/:root\.dark/g) ?? []).length,
  },
  "arbitrary-utility": {
    description: "任意值工具类",
    scope: "template",
    scan: (text) => (text.match(/\b[a-z][a-z0-9-]*-\[[^\]\s"'`]+\]/g) ?? []).length,
  },
  "palette-utility": {
    description: "调色板工具类",
    scope: "template",
    scan: (text) => (text.match(PALETTE_UTILITY) ?? []).length,
  },
};

function walkFiles(dir, out = []) {
  for (const entry of readdirSync(dir)) {
    if (entry === "node_modules" || entry === "dist") continue;
    const path = join(dir, entry);
    if (statSync(path).isDirectory()) {
      walkFiles(path, out);
      continue;
    }
    if (entry.endsWith(".spec.ts") || entry.endsWith(".test.ts")) continue;
    if (/\.(css|vue)$/.test(entry)) out.push(path);
  }
  return out;
}

const stripComments = (text) => text.replace(/\/\*[\s\S]*?\*\//g, "");

function extractBlocks(source) {
  const template = source
    .replace(/<script[\s\S]*?<\/script>/g, "")
    .replace(/<style[\s\S]*?<\/style>/g, "");
  const styles = [...source.matchAll(/<style[^>]*>([\s\S]*?)<\/style>/g)].map((m) => m[1]);
  return { template: stripComments(template), style: stripComments(styles.join("\n")) };
}

function scanFile(path) {
  const source = stripComments(readFileSync(path, "utf8"));
  const isVue = path.endsWith(".vue");
  const blocks = isVue ? extractBlocks(source) : null;
  const rel = relative(WEBUI_ROOT, path).split(sep).join("/");
  const counts = {};

  for (const [rule, definition] of Object.entries(RULES)) {
    let text;
    if (definition.scope === "template") {
      if (!isVue) continue;
      text = blocks.template;
    } else if (definition.scope === "style") {
      if (!isVue) continue;
      text = blocks.style;
    } else {
      text = isVue ? blocks.style : source;
    }
    const n = definition.scan(text, rel);
    if (n > 0) counts[rule] = n;
  }
  return { rel, counts };
}

const files = [join(WEBUI_ROOT, "packages"), join(WEBUI_ROOT, "vscode-extension")]
  .filter((dir) => {
    try {
      return statSync(dir).isDirectory();
    } catch {
      return false;
    }
  })
  .flatMap((dir) => walkFiles(dir));

const scan = files.map(scanFile);
const totals = {};
for (const file of scan) {
  for (const [rule, n] of Object.entries(file.counts)) {
    totals[rule] = (totals[rule] ?? 0) + n;
  }
}

const update = process.argv.includes("--update");

if (update) {
  const baseline = {
    $comment:
      "样式治理 ratchet 基线：数字是当前允许出现的违规次数，只能降不能升。重写用 node scripts/check-style-governance.mjs --update。",
    updated: new Date().toISOString().slice(0, 10),
    counts: Object.fromEntries(Object.keys(RULES).map((rule) => [rule, totals[rule] ?? 0])),
  };
  writeFileSync(BASELINE_PATH, `${JSON.stringify(baseline, null, 2)}\n`);
  console.log(`已写入基线 ${relative(process.cwd(), BASELINE_PATH)}`);
  for (const [rule, n] of Object.entries(baseline.counts)) console.log(`  ${rule.padEnd(18)} ${n}`);
  process.exit(0);
}

let baseline;
try {
  baseline = JSON.parse(readFileSync(BASELINE_PATH, "utf8"));
} catch (error) {
  console.error(`读取基线失败：${BASELINE_PATH}`);
  console.error(
    `${error.message}\n先运行 node scripts/check-style-governance.mjs --update 生成基线。`,
  );
  process.exit(1);
}

const failures = [];
const improvements = [];

console.log("样式治理检查（ratchet，违规数只降不升）");
for (const rule of Object.keys(RULES)) {
  const expected = baseline.counts?.[rule] ?? 0;
  const actual = totals[rule] ?? 0;
  const flag = actual > expected ? "✗" : actual < expected ? "↓" : "✓";
  console.log(`  ${flag} ${rule.padEnd(18)} ${String(actual).padStart(4)}  （基线 ${expected}）`);
  if (actual > expected) failures.push({ rule, expected, actual });
  if (actual < expected) improvements.push({ rule, expected, actual });
}

if (failures.length > 0) {
  console.error("\n以下规则出现新增违规：");
  for (const { rule, expected, actual } of failures) {
    console.error(
      `  ${rule}：${expected} → ${actual}（+${actual - expected}）—— ${RULES[rule].description}`,
    );
    const offenders = scan
      .filter((file) => file.counts[rule])
      .sort((a, b) => b.counts[rule] - a.counts[rule])
      .slice(0, 5)
      .map((file) => `      ${file.counts[rule]}  ${file.rel}`);
    console.error(offenders.join("\n"));
  }
  console.error(
    "\n新增违规必须修掉；确实无法避免时，在代码里就近说明原因，并在评审中确认后再 --update。",
  );
  process.exit(1);
}

if (improvements.length > 0) {
  console.log("\n有规则低于基线，可下调基线（node scripts/check-style-governance.mjs --update）：");
  for (const { rule, expected, actual } of improvements) {
    console.log(`  ${rule}：${expected} → ${actual}`);
  }
} else {
  console.log("\n未发现样式治理回归。");
}
