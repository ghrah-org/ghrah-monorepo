// SPDX-FileCopyrightText: 2026 chenxya <chenxya@ghrah.org>
//
// SPDX-License-Identifier: Apache-2.0

import type { Component } from "vue";
import { hasHostCapability, type HostCapabilities } from "./manifest.js";
import type { TsHalfSpec } from "./spec.js";

/**
 * TS 半插件注册表：按 `provides` 类型分桶（badge renderers / view renderers / commands）。
 * 登记时校验 spec `requires.host_capability` 是否被当前宿主满足（不满足拒登记）。
 *
 * 两级状态：
 * - spec 级（宿主装配后、加载前）：`register(spec, hostCaps)` 登记「已声明」桶；
 * - 运行时渲染器级（插件 `activate` 后）：`registerBadgeRenderer` 解析为宿主 Vue 组件；
 *   已声明未解析时 `resolveBadge` 返回 null（消费方走 raw 回退）。
 */

export interface PluginRegistryState {
  /** 已通过 host_capability 校验的 spec（plugin_id → spec）。 */
  readonly specs: ReadonlyMap<string, TsHalfSpec>;
  /** host_capability 不满足而被拒登记的 plugin_id 清单。 */
  readonly rejected: readonly string[];
}

export class PluginRegistry {
  private readonly _specs = new Map<string, TsHalfSpec>();
  private readonly _rejectedIds: string[] = [];
  private readonly _badgeDeclared = new Set<string>();
  private readonly _badgeComponents = new Map<string, Component>();
  private readonly _viewDeclared = new Set<string>();
  private readonly _viewComponents = new Map<string, Component>();
  private readonly _commands = new Map<string, string>();

  register(spec: TsHalfSpec, hostCaps?: HostCapabilities): boolean {
    if (!hasHostCapability(spec, hostCaps)) {
      this._rejectedIds.push(spec.plugin_id);
      return false;
    }
    this._specs.set(spec.plugin_id, spec);
    for (const kind of spec.provides.badge_renderers) this._badgeDeclared.add(kind);
    for (const name of spec.provides.view_renderers) this._viewDeclared.add(name);
    for (const name of spec.provides.commands) this._commands.set(name, spec.plugin_id);
    return true;
  }

  registerBadgeRenderer(kind: string, component: Component): void {
    this._badgeComponents.set(kind, component);
  }

  /** 已声明且已由插件 activate 解析出组件时返回组件；否则 null（raw 回退）。 */
  resolveBadge(kind: string): Component | null {
    return this._badgeComponents.get(kind) ?? null;
  }

  /** spec 已声明该 badge kind（无论是否已加载解析）。 */
  hasBadge(kind: string): boolean {
    return this._badgeDeclared.has(kind);
  }

  resolveViewRenderer(name: string): Component | null {
    return this._viewComponents.get(name) ?? null;
  }

  hasViewRenderer(name: string): boolean {
    return this._viewDeclared.has(name);
  }

  commandOwner(name: string): string | null {
    return this._commands.get(name) ?? null;
  }

  get state(): PluginRegistryState {
    return {
      specs: new Map(this._specs),
      rejected: [...this._rejectedIds],
    };
  }

  clear(): void {
    this._specs.clear();
    this._rejectedIds.length = 0;
    this._badgeDeclared.clear();
    this._badgeComponents.clear();
    this._viewDeclared.clear();
    this._viewComponents.clear();
    this._commands.clear();
  }
}
