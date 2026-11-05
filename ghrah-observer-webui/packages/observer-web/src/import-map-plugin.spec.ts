// SPDX-FileCopyrightText: 2026 chenxya <chenxya@ghrah.org>
//
// SPDX-License-Identifier: Apache-2.0

import { mkdtempSync, rmSync, writeFileSync } from "node:fs";
import { tmpdir } from "node:os";
import { join } from "node:path";
import { afterEach, beforeEach, describe, expect, it } from "vitest";
import { importMapPlugin } from "./import-map-plugin.js";

const HTML =
  '<!doctype html>\n<html>\n  <head>\n    <meta charset="UTF-8" />\n  </head>\n  <body></body>\n</html>\n';

let dir: string;

function writeImportMap(content: string): string {
  const path = join(dir, "import-map.json");
  writeFileSync(path, content);
  return path;
}

function transform(html: string, importMapPath?: string): string {
  const plugin = importMapPlugin({ importMapPath });
  const hook = plugin.transformIndexHtml as (html: string) => string | Promise<string>;
  return typeof hook === "function" ? (hook as (h: string) => string)(html) : html;
}

describe("importMapPlugin transformIndexHtml", () => {
  beforeEach(() => {
    dir = mkdtempSync(join(tmpdir(), "ghrah-import-map-"));
  });

  afterEach(() => {
    rmSync(dir, { recursive: true, force: true });
  });

  it("injects importmap script into head when imports non-empty", () => {
    const path = writeImportMap(JSON.stringify({ imports: { vue: "/plugins/shared/vue.js" } }));
    const result = transform(HTML, path);
    expect(result).toContain('<script type="importmap">');
    expect(result).toContain(
      JSON.stringify({ imports: { vue: "/plugins/shared/vue.js" } }).replace(/</g, "\\u003c"),
    );
    expect(result.indexOf('<script type="importmap">')).toBeLessThan(result.indexOf("</head>"));
  });

  it("does not inject when imports empty", () => {
    const path = writeImportMap(JSON.stringify({ imports: {} }));
    expect(transform(HTML, path)).toBe(HTML);
  });

  it("does not inject when file missing", () => {
    expect(transform(HTML, join(dir, "nope.json"))).toBe(HTML);
  });

  it("escapes < in JSON payload", () => {
    const path = writeImportMap(JSON.stringify({ imports: { evil: "/x<y>.js" } }));
    const result = transform(HTML, path);
    expect(result).not.toContain("/x<y>.js");
    expect(result).toContain("\\u003c");
  });
});
