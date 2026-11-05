// SPDX-FileCopyrightText: 2026 chenxya <chenxya@ghrah.org>
//
// SPDX-License-Identifier: Apache-2.0

/** ActionChain 能力语义分类（阶段 2 任务 2.3）。 */
export type AbilityClass = "write" | "read" | "converse" | "unknown";

/** 单能力名 → 分类：write_file/edit/apply_patch→write；read/list/search→read；conversation/send_message→converse。 */
function classifyAbilityName(name: string): AbilityClass {
  const n = name.toLowerCase();
  if (
    n.startsWith("write_") ||
    n.startsWith("edit_") ||
    n.startsWith("delete_") ||
    n.startsWith("move_") ||
    n.startsWith("apply_patch") ||
    n === "apply_patch"
  ) {
    return "write";
  }
  if (
    n.startsWith("read_") ||
    n.startsWith("list_") ||
    n.startsWith("query_") ||
    n.startsWith("search")
  ) {
    return "read";
  }
  if (n === "conversation" || n.startsWith("send_") || n.startsWith("broadcast_")) {
    return "converse";
  }
  return "unknown";
}

/** 分类优先级：写最显眼 > 读 > 通信 > 未知。 */
const PRIORITY: Record<AbilityClass, number> = { write: 3, read: 2, converse: 1, unknown: 0 };

/**
 * 节点能力集合 → 语义分类：多能力取最显眼（write > read > converse > unknown）；
 * 空集合归 unknown。
 */
export function abilityClass(abilityNames: string[]): AbilityClass {
  let best: AbilityClass = "unknown";
  for (const name of abilityNames) {
    const cls = classifyAbilityName(name);
    if (PRIORITY[cls] > PRIORITY[best]) best = cls;
  }
  return best;
}
