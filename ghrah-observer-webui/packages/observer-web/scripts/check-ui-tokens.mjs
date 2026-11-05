// SPDX-FileCopyrightText: 2026 chenxya <chenxya@ghrah.org>
//
// SPDX-License-Identifier: Apache-2.0

import { readFileSync } from "node:fs";

const css = readFileSync("src/style.css", "utf8");
const failures = [];

if (/font-size:\s*\d+px/.test(css)) {
  failures.push("style.css still contains a hardcoded px font-size");
}

const tokenizedClasses = [
  "section-eyebrow",
  "project-heading h3",
  "project-empty",
  "project-create-hint",
  "project-summary",
  "project-summary code",
  "tab-count",
  "empty-shortcuts",
];

for (const selector of tokenizedClasses) {
  const pattern = new RegExp(
    `${selector.replace(/[.*+?^${}()|[\]\\]/g, "\\$&")}\\s*\\{[\\s\\S]*?\\}`,
    "g",
  );
  const blocks = [...css.matchAll(pattern)].map((match) => match[0]);
  if (!blocks.some((block) => block.includes("--font-ui-"))) {
    failures.push(`${selector} must use a --font-ui-* token`);
  }
}

if (failures.length > 0) {
  console.error(failures.join("\n"));
  process.exit(1);
}
