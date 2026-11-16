// SPDX-FileCopyrightText: 2026 chenxya <chenxya@ghrah.org>
//
// SPDX-License-Identifier: Apache-2.0

/** Project 管理 REST 的特性开关。
 *
 * 开关在「连接时」读取一次：切换模式需要重连（刷新页面）。默认值在 R4
 * 切到 ``rest``，保留 ``ws`` 回退；非法值静默回落默认。
 */

export type ProjectApiMode = "ws" | "rest";

/** 默认走 REST（T4/R4 切换点；回退只需把这里改回 ``"ws"`` 或设环境变量）。 */
export const DEFAULT_PROJECT_API_MODE: ProjectApiMode = "rest";

export function resolveProjectApiMode(): ProjectApiMode {
  const raw =
    (import.meta.env?.VITE_GHRAH_PROJECT_API as string | undefined) ??
    (globalThis as { __GHRAH_PROJECT_API__?: string }).__GHRAH_PROJECT_API__;
  if (raw === "rest" || raw === "ws") return raw;
  return DEFAULT_PROJECT_API_MODE;
}
