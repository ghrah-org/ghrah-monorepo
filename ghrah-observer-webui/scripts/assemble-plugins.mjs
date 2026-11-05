#!/usr/bin/env node

// SPDX-FileCopyrightText: 2026 chenxya <chenxya@ghrah.org>
//
// SPDX-License-Identifier: Apache-2.0

// ghrah 宿主插件装配脚本：
//   1. 扫描 GHRAH_OBSERVER_PLUGINS_DIR（默认 ~/.ghrah/plugins）下
//      <plugin_id>/<version>/plugin.json + entry 产物；
//   2. 校验（复用 @ghrah/plugin-registry 的 TsHalfSpecSchema）；
//   3. 复制到 packages/observer-web/public/plugins/<plugin_id>/<version>/；
//   4. 产出 plugins-manifest.json（plugins + urls）与 import-map.json（单一产物）；
//   5. 复制仓库静态 shim assets/plugins-shared/vue.js → public/plugins/shared/vue.js。
// 约束：cwd 无关（以 import.meta.url 锚定路径——predev 的 cwd 是 packages/observer-web）；
//       无插件目录/空目录/装配失败 → 写出空 manifest + 空 imports（退出 0，仅 warning），
//       宿主零插件照常运行（零隐式）。
import {
  copyFileSync,
  existsSync,
  mkdirSync,
  readdirSync,
  readFileSync,
  rmSync,
  writeFileSync,
} from "node:fs";
import { dirname, join, resolve } from "node:path";
import { fileURLToPath } from "node:url";
import { pathToFileURL } from "node:url";

const WEBUI_ROOT = resolve(dirname(fileURLToPath(import.meta.url)), "..");
const PLUGINS_PUBLIC_DIR = join(WEBUI_ROOT, "packages", "observer-web", "public", "plugins");
const SHIM_SOURCE = join(WEBUI_ROOT, "assets", "plugins-shared", "vue.js");
const PLUGINS_SOURCE_DIR =
  process.env.GHRAH_OBSERVER_PLUGINS_DIR ?? join(process.env.HOME ?? "~", ".ghrah", "plugins");

function warn(message) {
  console.warn(`[assemble-plugins] warning: ${message}`);
}

async function loadSpecValidator() {
  const registryUrl = pathToFileURL(
    join(WEBUI_ROOT, "packages", "plugin-registry", "dist", "index.js"),
  );
  const { TsHalfSpecSchema } = await import(registryUrl.href);
  return (raw) => TsHalfSpecSchema.parse(raw);
}

function collectPlugins(sourceDir, validate) {
  const plugins = [];
  if (!existsSync(sourceDir)) return plugins;
  for (const pluginIdEntry of readdirSync(sourceDir, { withFileTypes: true })) {
    if (!pluginIdEntry.isDirectory()) continue;
    const versions = readdirSync(join(sourceDir, pluginIdEntry.name), { withFileTypes: true })
      .filter((entry) => entry.isDirectory())
      .map((entry) => entry.name)
      .sort()
      .reverse(); // P0 单版本：取字典序最新且校验通过的版本。
    for (const version of versions) {
      const pluginDir = join(sourceDir, pluginIdEntry.name, version);
      const specPath = join(pluginDir, "plugin.json");
      if (!existsSync(specPath)) continue;
      try {
        const spec = validate(JSON.parse(readFileSync(specPath, "utf8")));
        if (spec.plugin_id !== pluginIdEntry.name || spec.version !== version) {
          warn(`skip ${pluginIdEntry.name}/${version}: spec id/version mismatch`);
          continue;
        }
        if (!existsSync(join(pluginDir, spec.entry))) {
          warn(`skip ${pluginIdEntry.name}/${version}: entry not found: ${spec.entry}`);
          continue;
        }
        plugins.push({ spec, pluginDir });
        break;
      } catch (err) {
        warn(`skip ${pluginIdEntry.name}/${version}: invalid plugin.json (${err.message})`);
      }
    }
  }
  return plugins;
}

function copyTree(from, to) {
  mkdirSync(to, { recursive: true });
  for (const entry of readdirSync(from, { withFileTypes: true })) {
    if (entry.isDirectory()) copyTree(join(from, entry.name), join(to, entry.name));
    else copyFileSync(join(from, entry.name), join(to, entry.name));
  }
}

function writeEmptyOutputs() {
  rmSync(PLUGINS_PUBLIC_DIR, { recursive: true, force: true });
  mkdirSync(PLUGINS_PUBLIC_DIR, { recursive: true });
  writeFileSync(
    join(PLUGINS_PUBLIC_DIR, "plugins-manifest.json"),
    `${JSON.stringify({ plugins: [], urls: {} }, null, 2)}\n`,
  );
  writeFileSync(
    join(PLUGINS_PUBLIC_DIR, "import-map.json"),
    `${JSON.stringify({ imports: {} }, null, 2)}\n`,
  );
}

async function main() {
  const validate = await loadSpecValidator();
  const collected = collectPlugins(PLUGINS_SOURCE_DIR, validate);

  rmSync(PLUGINS_PUBLIC_DIR, { recursive: true, force: true });
  mkdirSync(PLUGINS_PUBLIC_DIR, { recursive: true });

  const manifestPlugins = [];
  const urls = {};
  for (const { spec, pluginDir } of collected) {
    copyTree(pluginDir, join(PLUGINS_PUBLIC_DIR, spec.plugin_id, spec.version));
    manifestPlugins.push(spec);
    urls[spec.plugin_id] = `/plugins/${spec.plugin_id}/${spec.version}/${spec.entry}`;
  }

  // 共享 shim：import map 的 "vue" 映射目标。
  if (existsSync(SHIM_SOURCE)) {
    mkdirSync(join(PLUGINS_PUBLIC_DIR, "shared"), { recursive: true });
    copyFileSync(SHIM_SOURCE, join(PLUGINS_PUBLIC_DIR, "shared", "vue.js"));
  } else {
    warn("assets/plugins-shared/vue.js missing: skipping shared shim");
  }

  writeFileSync(
    join(PLUGINS_PUBLIC_DIR, "plugins-manifest.json"),
    `${JSON.stringify({ plugins: manifestPlugins, urls }, null, 2)}\n`,
  );
  // 有插件才有 import map（imports 非空才会被注入 index.html）。
  const imports = manifestPlugins.length > 0 ? { vue: "/plugins/shared/vue.js" } : {};
  writeFileSync(
    join(PLUGINS_PUBLIC_DIR, "import-map.json"),
    `${JSON.stringify({ imports }, null, 2)}\n`,
  );

  console.log(
    `[assemble-plugins] ${manifestPlugins.length} plugin(s) assembled from ${PLUGINS_SOURCE_DIR} -> ${PLUGINS_PUBLIC_DIR}`,
  );
}

main().catch((err) => {
  // 装配失败不阻断 webui：产出空产物后退出 0（D4）。
  warn(`assembly failed: ${err.message}`);
  try {
    writeEmptyOutputs();
  } catch (fallbackErr) {
    warn(`fallback empty outputs failed: ${fallbackErr.message}`);
  }
});
