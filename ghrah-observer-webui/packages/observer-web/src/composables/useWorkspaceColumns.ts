// SPDX-FileCopyrightText: 2026 chenxya <chenxya@ghrah.org>
//
// SPDX-License-Identifier: Apache-2.0

import { onScopeDispose, ref } from "vue";

export type WorkspaceColumnMode = "wide" | "mid" | "rail" | "exclusive";

const WIDE_QUERY = "(min-width: 90rem)";
const MID_QUERY = "(min-width: 75rem)";
const RAIL_QUERY = "(min-width: 64rem)";

export function readWorkspaceColumnMode(
  matches: (query: string) => boolean = defaultMatches,
): WorkspaceColumnMode {
  if (matches(WIDE_QUERY)) return "wide";
  if (matches(MID_QUERY)) return "mid";
  if (matches(RAIL_QUERY)) return "rail";
  return "exclusive";
}

function defaultMatches(query: string): boolean {
  if (typeof globalThis.matchMedia !== "function") return query === WIDE_QUERY;
  return globalThis.matchMedia(query).matches;
}

// Viewport band for the workspace shell. Missing matchMedia stays on the wide
// band so tests and embedded views keep the manual sidebars.
export function useWorkspaceColumns() {
  const mode = ref<WorkspaceColumnMode>(readWorkspaceColumnMode());
  const lists: MediaQueryList[] = [];

  function refresh() {
    mode.value = readWorkspaceColumnMode();
  }

  if (typeof globalThis.matchMedia === "function") {
    for (const query of [WIDE_QUERY, MID_QUERY, RAIL_QUERY]) {
      const list = globalThis.matchMedia(query);
      list.addEventListener("change", refresh);
      lists.push(list);
    }
  }

  onScopeDispose(() => {
    for (const list of lists) list.removeEventListener("change", refresh);
  });

  return mode;
}
