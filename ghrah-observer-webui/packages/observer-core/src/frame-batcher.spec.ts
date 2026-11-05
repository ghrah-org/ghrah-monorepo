// SPDX-FileCopyrightText: 2026 chenxya <chenxya@ghrah.org>
//
// SPDX-License-Identifier: Apache-2.0

import { describe, expect, it } from "vitest";
import { createFrameBatcher, createSyncBatcher } from "./frame-batcher.js";

describe("createFrameBatcher", () => {
  it("coalesces same-frame adds into a single scheduled flush", () => {
    const scheduled: Array<() => void> = [];
    const batcher = createFrameBatcher((flush) => scheduled.push(flush));

    let calls = 0;
    for (let i = 0; i < 5; i++) {
      batcher.add(() => {
        calls += 1;
      });
    }
    // 只调度一次；未 flush 前不执行
    expect(scheduled).toHaveLength(1);
    expect(calls).toBe(0);
    expect(batcher.size).toBe(5);

    scheduled[0]!();
    expect(calls).toBe(5);
    expect(batcher.size).toBe(0);
    // flush 后队列已清空，再次调度不会重复执行
    scheduled[0]!();
    expect(calls).toBe(5);
  });

  it("preserves FIFO order within a flush", () => {
    const order: number[] = [];
    const batcher = createFrameBatcher((flush) => flush());
    for (let i = 0; i < 4; i++) {
      batcher.add(() => order.push(i));
    }
    expect(order).toEqual([0, 1, 2, 3]);
  });

  it("flush() applies pending callbacks immediately", () => {
    const scheduled: Array<() => void> = [];
    const batcher = createFrameBatcher((flush) => scheduled.push(flush));
    const seen: string[] = [];
    batcher.add(() => seen.push("a"));
    // 手动 flush 优先于已调度的回调
    batcher.flush();
    expect(seen).toEqual(["a"]);
    expect(batcher.size).toBe(0);
    // 已调度的回调再触发时队列为空 → 不重复执行
    scheduled[0]!();
    expect(seen).toEqual(["a"]);
  });

  it("cancel() drops pending callbacks and neutralizes scheduled flush", () => {
    const scheduled: Array<() => void> = [];
    const batcher = createFrameBatcher((flush) => scheduled.push(flush));

    let calls = 0;
    batcher.add(() => {
      calls += 1;
    });
    batcher.cancel();
    expect(batcher.size).toBe(0);

    // 已调度的回调触发时队列为空 → 无副作用
    scheduled[0]!();
    expect(calls).toBe(0);
  });

  it("schedules again after a flush for a new frame", () => {
    const scheduled: Array<() => void> = [];
    const batcher = createFrameBatcher((flush) => scheduled.push(flush));

    batcher.add(() => {});
    scheduled.splice(0, 1)[0]!();
    batcher.add(() => {});
    // 新帧重新调度
    expect(scheduled).toHaveLength(1);
  });
});

describe("createSyncBatcher", () => {
  it("executes immediately (sync semantics passthrough)", () => {
    const batcher = createSyncBatcher();
    const seen: number[] = [];
    batcher.add(() => seen.push(1));
    expect(seen).toEqual([1]);
    batcher.cancel();
    batcher.flush();
    expect(seen).toEqual([1]);
    expect(batcher.size).toBe(0);
  });
});
