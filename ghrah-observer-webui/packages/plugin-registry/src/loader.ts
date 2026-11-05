// SPDX-FileCopyrightText: 2026 chenxya <chenxya@ghrah.org>
//
// SPDX-License-Identifier: Apache-2.0

import type { Component } from "vue";
import type { PluginRegistry } from "./registry.js";

/**
 * 插件加载适配器：动态 `import()` 产物 URL 并调用其 `activate(api)`。
 * 契约（D7）：插件 ESM 导出 `activate(api: PluginHostApi): void | (() => void)`；
 * 缺失/加载抛错/activate 抛错 → 返回归一化 error（不抛出、不炸宿主），
 * 不注册渲染器（消费方走 raw 回退）。P0 不支持 deactivate（返回值忽略）。
 */

export interface PluginHostApi {
  readonly pluginId: string;
  readonly version: string;
  registerBadgeRenderer(kind: string, component: Component): void;
  /** 注册表只读视图（查询/探测，不暴露登记写路径）。 */
  readonly registry: PluginRegistry;
}

export interface PluginLoadSuccess {
  readonly ok: true;
  readonly pluginId: string;
}

export interface PluginLoadFailure {
  readonly ok: false;
  readonly pluginId: string;
  readonly error: string;
}

export type PluginLoadResult = PluginLoadSuccess | PluginLoadFailure;

interface PluginModule {
  activate?: unknown;
}

/**
 * 动态导入间接层：避免打包器（Vite/Rollup）在构建期拦截并改写
 * 运行时 URL 导入（dev 下 `/public` 资产会被 import-analysis 拒绝，
 * `@vite-ignore` 注释经库构建后不可靠）。构造器形式的动态导入对
 * 打包器不可见，由浏览器原生执行（经 import map 解析裸说明符）。
 * 测试环境（vitest 的 vite-node 运行时会剥离构造器内的 import）可经
 * `loadPlugin` 第三参注入宿主导入器。
 */
const browserImport = new Function("url", "return import(url);") as (
  url: string,
) => Promise<PluginModule>;

export interface LoadPluginOptions {
  /** 注入宿主动态导入器（测试用；默认构造器间接导入）。 */
  importModule?: (url: string) => Promise<PluginModule>;
}

export async function loadPlugin(
  url: string,
  api: PluginHostApi,
  options: LoadPluginOptions = {},
): Promise<PluginLoadResult> {
  const importModule = options.importModule ?? browserImport;
  let module: PluginModule;
  try {
    module = await importModule(url);
  } catch (err) {
    return { ok: false, pluginId: api.pluginId, error: `import failed: ${errorMessage(err)}` };
  }
  if (typeof module.activate !== "function") {
    return { ok: false, pluginId: api.pluginId, error: "module does not export activate()" };
  }
  try {
    (module.activate as (api: PluginHostApi) => void | (() => void))(api);
  } catch (err) {
    return { ok: false, pluginId: api.pluginId, error: `activate() threw: ${errorMessage(err)}` };
  }
  return { ok: true, pluginId: api.pluginId };
}

function errorMessage(err: unknown): string {
  return err instanceof Error ? err.message : String(err);
}
