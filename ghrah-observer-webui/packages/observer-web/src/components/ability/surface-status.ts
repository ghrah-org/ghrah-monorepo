// SPDX-FileCopyrightText: 2026 chenxya <chenxya@ghrah.org>
//
// SPDX-License-Identifier: Apache-2.0

import type { AbilityAdminView } from "@ghrah/protocol";

/**
 * 工具面的诚实状态。
 *
 * - `unobserved`：后端明确返回「没有宿主观测」（`current_runtime` 为 `null`）。
 * - `unmaterialized`：运行快照是空数组，且待添加集合恰好等于全部生效工具，
 *   说明这个 Agent 的运行工具面尚未物化（首次对话后才生成），不是重启需求。
 * - `restart`：运行快照非空且存在待生效差异，或后端标记需要重启。
 * - `ready`：没有待生效差异。
 */
export type SurfaceStatus = "unobserved" | "unmaterialized" | "restart" | "ready";

export function surfaceStatus(
  admin: Pick<AbilityAdminView, "lifecycle" | "surface">,
): SurfaceStatus {
  const surface = admin.surface;
  if (surface.current_runtime === null) return "unobserved";
  const added = surface.pending_respawn.added;
  const removed = surface.pending_respawn.removed;
  if (surface.current_runtime.length === 0 && added.length > 0) {
    const effective = surface.effective.map((entry) => entry.tool_name);
    if (
      added.length === effective.length &&
      effective.every((toolName) => added.includes(toolName))
    ) {
      return "unmaterialized";
    }
  }
  if (admin.lifecycle.requires_respawn || added.length > 0 || removed.length > 0) {
    return "restart";
  }
  return "ready";
}
