// SPDX-FileCopyrightText: 2026 chenxya <chenxya@ghrah.org>
//
// SPDX-License-Identifier: Apache-2.0

import { readdirSync, readFileSync, statSync } from "node:fs";
import { join, relative } from "node:path";

const root = process.cwd();
const src = join(root, "src");
const failures = [];

// Non-localizable glyphs that are allowed as bare template text nodes.
const allowedTextNodes = new Set(["G"]); // brand mark in app.vue

function walk(dir) {
  for (const entry of readdirSync(dir)) {
    const path = join(dir, entry);
    const stat = statSync(path);
    if (stat.isDirectory()) {
      if (["locales", "test"].includes(entry)) continue;
      walk(path);
      continue;
    }
    if (!/\.(vue|ts)$/.test(entry) || entry.endsWith(".spec.ts")) continue;
    let text = readFileSync(path, "utf8");
    text = text.replace(/<!--[\s\S]*?-->/g, "");
    text = text.replace(/\/\*[\s\S]*?\*\//g, "");
    text = text.replace(/\/\/.*$/gm, "");
    const han = text.match(/[\p{Script=Han}]/u);
    if (han) {
      failures.push(`${relative(root, path)}:${text.slice(0, han.index).split("\n").length}`);
    }
    if (entry.endsWith(".vue")) {
      // Scan template text nodes for bare English copy; script/style blocks
      // are stripped so code and CSS selectors cannot false-positive.
      const template = text
        .replace(/<script[\s\S]*?<\/script>/g, "")
        .replace(/<style[\s\S]*?<\/style>/g, "");
      for (const match of template.matchAll(/>\s*([A-Za-z][^<{}]*?)\s*</g)) {
        if (allowedTextNodes.has(match[1])) continue;
        const line = template.slice(0, match.index).split("\n").length;
        failures.push(`${relative(root, path)}:${line} bare text "${match[1]}"`);
      }
    }
  }
}

walk(src);

if (failures.length > 0) {
  console.error(`Hardcoded UI copy found outside locales:\n${failures.join("\n")}`);
  process.exit(1);
}
