// SPDX-FileCopyrightText: 2026 chenxya <chenxya@ghrah.org>
//
// SPDX-License-Identifier: Apache-2.0

import type { ActionNode } from "@ghrah/protocol";

import { isCompactNode } from "./compact-node.js";

type ActionChainAbilityClass = "write" | "read" | "converse" | "unknown";
export type AbilityClass =
  ActionChainAbilityClass | "think" | "execute" | "tool" | "spawn" | "compact";

function classifyAbilityName(name: string): ActionChainAbilityClass {
  const n = name.toLowerCase();
  if (
    n.startsWith("write_") ||
    n.startsWith("edit_") ||
    n.startsWith("delete_") ||
    n.startsWith("move_") ||
    n.startsWith("apply_patch")
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

function classifyNodeAbilityName(name: string): AbilityClass {
  const member = (name.split(".").at(-1) ?? name).toLowerCase();
  const existing = classifyAbilityName(member);
  if (existing !== "unknown") return existing;

  if (/^(think|reason|plan)(_|$)/.test(member)) return "think";
  if (/^(execute|run|shell|command)(_|$)/.test(member)) return "execute";
  if (/^(spawn_agent|delegate)(_|$)/.test(member)) return "spawn";
  if (/^(mcp|tool)_/.test(member)) return "tool";
  if (member === "send" || member === "agent_response") return "converse";

  return "unknown";
}

const PRIORITY: Record<AbilityClass, number> = {
  compact: 8,
  write: 7,
  execute: 6,
  spawn: 5,
  read: 4,
  converse: 3,
  think: 2,
  tool: 1,
  unknown: 0,
};

function mostProminent(
  abilityNames: readonly string[],
  classify: (name: string) => AbilityClass,
): AbilityClass {
  let best: AbilityClass = "unknown";
  for (const name of abilityNames) {
    const cls = classify(name);
    if (PRIORITY[cls] > PRIORITY[best]) best = cls;
  }
  return best;
}

/** 画布与详情抽屉使用现有的四档样式。 */
export function abilityClass(abilityNames: string[]): AbilityClass {
  return mostProminent(abilityNames, classifyAbilityName);
}

export function nodeClass(node: ActionNode): AbilityClass {
  if (isCompactNode(node)) return "compact";
  return mostProminent(node.ability_names, classifyNodeAbilityName);
}

export function chainToken(cls: AbilityClass): string {
  return `var(--chain-${cls === "execute" ? "tool" : cls})`;
}
