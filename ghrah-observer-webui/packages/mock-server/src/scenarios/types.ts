// SPDX-FileCopyrightText: 2026 chenxya <chenxya@ghrah.org>
//
// SPDX-License-Identifier: Apache-2.0

import type { CommandType } from "@ghrah/protocol";
import type { MockState } from "../state.js";

export interface ScenarioContext {
  state: MockState;
  /** 走命令处理路径（状态更新 + 事件广播），等价于外部命令到达。 */
  apply(commandType: CommandType, payload: Record<string, unknown>): void;
}

export type TimelineAction = (ctx: ScenarioContext) => void;

/** 时间线条目：[距场景启动的绝对偏移 ms, 动作]。 */
export type TimelineEntry = [number, TimelineAction];

export interface Scenario {
  name: string;
  description?: string;
  /** 初始状态注入（项目/room 等，启动时同步执行）。 */
  setup?(state: MockState): void;
  /** 时间线回放动作序列。 */
  timeline: TimelineEntry[];
}

/** 执行场景：setup 同步执行，timeline 按偏移调度；返回取消函数。 */
export function runScenario(scenario: Scenario, ctx: ScenarioContext): () => void {
  scenario.setup?.(ctx.state);
  const timers: ReturnType<typeof setTimeout>[] = [];
  for (const [offsetMs, action] of scenario.timeline) {
    timers.push(setTimeout(() => action(ctx), offsetMs));
  }
  return () => {
    for (const timer of timers) clearTimeout(timer);
  };
}
