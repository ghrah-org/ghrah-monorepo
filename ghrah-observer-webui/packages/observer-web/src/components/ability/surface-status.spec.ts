// SPDX-FileCopyrightText: 2026 chenxya <chenxya@ghrah.org>
//
// SPDX-License-Identifier: Apache-2.0

import type { AbilityAdminView } from "@ghrah/protocol";
import { describe, expect, it } from "vitest";
import { surfaceStatus } from "./surface-status";

function admin(overrides: {
  runtime: string[] | null;
  effective?: string[];
  added?: string[];
  removed?: string[];
  requiresRespawn?: boolean;
}): Pick<AbilityAdminView, "lifecycle" | "surface"> {
  return {
    lifecycle: {
      state: "running",
      requires_respawn: overrides.requiresRespawn ?? false,
      pending_reasons: [],
    },
    surface: {
      mode: "auto",
      static: [],
      effective: (overrides.effective ?? ["read_file", "write_file"]).map((toolName) => ({
        tool_name: toolName,
        member: toolName,
        package: "builtin:files",
        static: false,
        available: true,
      })),
      current_runtime: overrides.runtime,
      pending_respawn: { added: overrides.added ?? [], removed: overrides.removed ?? [] },
    },
  };
}

describe("surfaceStatus", () => {
  it("reports unobserved when the host has no runtime snapshot", () => {
    expect(surfaceStatus(admin({ runtime: null, added: ["read_file"] }))).toBe("unobserved");
  });

  it("treats a fully pending empty snapshot as unmaterialized instead of restart", () => {
    const view = admin({ runtime: [], added: ["read_file", "write_file"], requiresRespawn: true });
    expect(surfaceStatus(view)).toBe("unmaterialized");
  });

  it("keeps restart when the empty snapshot only partially covers the effective tools", () => {
    const view = admin({ runtime: [], added: ["read_file"], requiresRespawn: true });
    expect(surfaceStatus(view)).toBe("restart");
  });

  it("keeps restart when the runtime snapshot has pending additions", () => {
    const view = admin({
      runtime: ["read_file"],
      added: ["write_file"],
      requiresRespawn: true,
    });
    expect(surfaceStatus(view)).toBe("restart");
  });

  it("keeps restart when the runtime snapshot has pending removals", () => {
    const view = admin({
      runtime: ["read_file", "write_file"],
      removed: ["write_file"],
      requiresRespawn: true,
    });
    expect(surfaceStatus(view)).toBe("restart");
  });

  it("trusts the lifecycle flag when there is no surface diff", () => {
    const view = admin({
      runtime: ["read_file", "write_file"],
      requiresRespawn: true,
    });
    expect(surfaceStatus(view)).toBe("restart");
  });

  it("reports ready when the runtime snapshot matches the effective tools", () => {
    const view = admin({ runtime: ["read_file", "write_file"] });
    expect(surfaceStatus(view)).toBe("ready");
  });

  it("reports ready for an empty assembly without tools", () => {
    const view = admin({ runtime: [], effective: [] });
    expect(surfaceStatus(view)).toBe("ready");
  });
});
