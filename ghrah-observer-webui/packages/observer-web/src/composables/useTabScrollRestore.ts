// SPDX-FileCopyrightText: 2026 chenxya <chenxya@ghrah.org>
//
// SPDX-License-Identifier: Apache-2.0

import { nextTick, onActivated, onMounted, type Ref, type WatchSource, watch } from "vue";

/** 纵向滚动恢复目标：number 为绝对 scrollTop；"bottom" 落底；null 恢复上次记录的用户滚动位置。 */
export type ScrollRestoreTarget = number | "bottom" | null;

/** 横向滚动恢复目标：number 为绝对 scrollLeft；"end" 落右端；null 恢复上次记录的用户滚动位置。 */
export type HorizontalRestoreTarget = number | "end" | null;

export interface TabScrollRestoreOptions {
  /** 内容规模信号（如条目数）；变化后在下一 tick 重放恢复目标，覆盖补拉/钳制后内容异步到达的场景。 */
  contentKey?: WatchSource<unknown>;
  /** 纵向恢复目标；缺省恢复记录位置。 */
  target?: () => ScrollRestoreTarget;
  /** 横向恢复目标；缺省恢复记录位置（横向不可滚动的面板恒为 0，行为不变）。 */
  horizontalTarget?: () => HorizontalRestoreTarget;
}

/**
 * 标签面板滚动位置缓存（配合 KeepAlive 使用）。滚动事件里持续记录位置——
 * KeepAlive 停用时 DOM 脱离文档、滚动偏移会被浏览器重置，无法依赖停用时机读取；
 * 挂载、激活或内容变化时重放恢复目标（纵向与横向各自独立）。
 */
export function useTabScrollRestore(
  container: Ref<HTMLElement | null | undefined>,
  options: TabScrollRestoreOptions = {},
): { onScroll: () => void; applyScroll: () => void } {
  let savedTop = 0;
  let savedLeft = 0;
  // 最近一次程序式赋值的生效值：滚动事件与之吻合视为回声，不算用户滚动
  let programmedTop: number | null = null;
  let programmedLeft: number | null = null;

  function maxScrollTop(el: HTMLElement): number {
    return Math.max(0, el.scrollHeight - el.clientHeight);
  }

  function maxScrollLeft(el: HTMLElement): number {
    return Math.max(0, el.scrollWidth - el.clientWidth);
  }

  /** 重放恢复目标；内容未渲染或被钳制时静默跳过，待 contentKey 变化再重放。 */
  function applyScroll(): void {
    const el = container.value;
    if (!el) return;

    const vertical = options.target?.() ?? null;
    const desiredTop = vertical === "bottom" ? maxScrollTop(el) : (vertical ?? savedTop);
    if (Math.abs(el.scrollTop - desiredTop) > 1) {
      el.scrollTop = desiredTop;
      programmedTop = el.scrollTop;
    }

    const horizontal = options.horizontalTarget?.() ?? null;
    const desiredLeft = horizontal === "end" ? maxScrollLeft(el) : (horizontal ?? savedLeft);
    if (Math.abs(el.scrollLeft - desiredLeft) > 1) {
      el.scrollLeft = desiredLeft;
      programmedLeft = el.scrollLeft;
    }
  }

  /** 记录用户滚动位置；须由模板 `@scroll.passive` 绑定。 */
  function onScroll(): void {
    const el = container.value;
    if (!el) return;

    // 与最近一次程序式赋值吻合的滚动事件是回声，不得覆盖用户位置。
    if (programmedTop === null || Math.abs(el.scrollTop - programmedTop) > 1) {
      savedTop = el.scrollTop;
    }
    programmedTop = null;

    if (programmedLeft === null || Math.abs(el.scrollLeft - programmedLeft) > 1) {
      savedLeft = el.scrollLeft;
    }
    programmedLeft = null;
  }

  onMounted(() => {
    void nextTick(applyScroll);
  });

  onActivated(() => {
    void nextTick(applyScroll);
  });

  if (options.contentKey) {
    watch(options.contentKey, () => {
      void nextTick(applyScroll);
    });
  }

  return { onScroll, applyScroll };
}
