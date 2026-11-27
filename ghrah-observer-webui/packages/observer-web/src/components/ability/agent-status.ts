// SPDX-FileCopyrightText: 2026 chenxya <chenxya@ghrah.org>
//
// SPDX-License-Identifier: Apache-2.0

/**
 * Agent 生命周期状态的文案键映射。
 *
 * 观察端读到的状态来自三处，取值域不同：记录里的 `desired_state`、
 * REST 运行态的 `availability`、记录/live 的 `runtime_status`。这里统一收敛成
 * `assembly.agent.*` 下的稳定键，组件不再直接拼原始枚举。
 */

export type DesiredStateKey = "running" | "stopped" | "unknown";
export type AvailabilityKey = "online" | "offline" | "unknown";
export type RuntimeStatusKey = "pending" | "running" | "stopped" | "failed" | "lost" | "unknown";

export function desiredStateKey(state: string | null | undefined): DesiredStateKey {
  if (state === "running" || state === "stopped") return state;
  return "unknown";
}

export function availabilityKey(availability: string | null | undefined): AvailabilityKey {
  if (availability === "online" || availability === "offline") return availability;
  return "unknown";
}

export function runtimeStatusKey(status: string | null | undefined): RuntimeStatusKey {
  if (
    status === "pending" ||
    status === "running" ||
    status === "stopped" ||
    status === "failed" ||
    status === "lost"
  ) {
    return status;
  }
  // 旧记录里的 "active" 与 live 事件共用「运行中」。
  if (status === "active") return "running";
  return "unknown";
}

/**
 * 是否在线。
 *
 * REST 运行态优先；事件推进的 live 状态兜底（`agent_spawned` 会把它置为
 * running）。两者都不认识时按离线处理，启动按钮的可用性再按 availability
 * 单独收紧（unknown 不可启动）。
 */
export function isAgentOnline(
  availability: string | null | undefined,
  liveStatus: string | null | undefined,
): boolean {
  return availability === "online" || liveStatus === "running" || liveStatus === "active";
}
