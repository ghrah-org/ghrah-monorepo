// SPDX-FileCopyrightText: 2026 chenxya <chenxya@ghrah.org>
//
// SPDX-License-Identifier: Apache-2.0

/**
 * Frame batcher：高频 WS 事件合帧分发（性能红线 4）。
 *
 * 同一帧（rAF 或微任务窗口）内到达的多个事件回调合并为一次 flush，
 * 避免事件风暴下每条消息同步触发一次 store 更新 + 渲染。
 * flush 内保持 FIFO 顺序（事件语义顺序不变，dedup 仍在 store 层）。
 */

export type ScheduleFn = (flush: () => void) => void;

export interface FrameBatcher {
  /** 入队一个回调；若本帧尚未调度则调度一次 flush。 */
  add(fn: () => void): void;
  /** 立即冲刷全部待处理回调（测试/同步语义用）。 */
  flush(): void;
  /** 丢弃全部待处理回调（unbind 时防泄漏写入已清空的 store）。 */
  cancel(): void;
  /** 当前待处理回调数。 */
  readonly size: number;
}

function defaultSchedule(flush: () => void): void {
  const g = globalThis as {
    requestAnimationFrame?: unknown;
    queueMicrotask?: unknown;
  };
  if (typeof g.requestAnimationFrame === "function") {
    (g.requestAnimationFrame as (cb: () => void) => void)(flush);
  } else if (typeof g.queueMicrotask === "function") {
    (g.queueMicrotask as (cb: () => void) => void)(flush);
  } else {
    Promise.resolve().then(flush);
  }
}

export function createFrameBatcher(schedule?: ScheduleFn): FrameBatcher {
  const queue: Array<() => void> = [];
  const sched: ScheduleFn = schedule ?? defaultSchedule;
  let scheduled = false;

  function runQueue(): void {
    scheduled = false;
    if (queue.length === 0) return;
    const fns = queue.splice(0, queue.length);
    for (const fn of fns) fn();
  }

  return {
    add(fn: () => void): void {
      queue.push(fn);
      if (!scheduled) {
        scheduled = true;
        sched(runQueue);
      }
    },
    flush: runQueue,
    cancel(): void {
      queue.length = 0;
      scheduled = false;
    },
    get size(): number {
      return queue.length;
    },
  };
}

/** 立即冲刷的直通 batcher（测试/同步语义兼容形态）。 */
export function createSyncBatcher(): FrameBatcher {
  return {
    add(fn: () => void): void {
      fn();
    },
    flush(): void {},
    cancel(): void {},
    get size(): number {
      return 0;
    },
  };
}
