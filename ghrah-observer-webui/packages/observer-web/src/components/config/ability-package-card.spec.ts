// SPDX-FileCopyrightText: 2026 chenxya <chenxya@ghrah.org>
//
// SPDX-License-Identifier: Apache-2.0

// @vitest-environment happy-dom

import type { AbilityPackageGroupVersionView } from "@ghrah/protocol";
import { mount, type VueWrapper } from "@vue/test-utils";
import { describe, expect, it } from "vitest";
import AbilityPackageCard from "./ability-package-card.vue";

/**
 * ``operation_count`` / ``skill_count`` 尚未进入契约，测试里直接构造带这两个字段的行，
 * 用来锁住“有值按工具·技能展示、缺值回退成员数”的显示规则。
 */
type CountedVersion = AbilityPackageGroupVersionView & {
  operation_count?: number;
  skill_count?: number;
};

function version(extra: Partial<CountedVersion> = {}): CountedVersion {
  return {
    version: "1.0.0",
    origin: "builtin",
    lock_state: "builtin",
    problem: null,
    shadowed_builtin: false,
    ability_type: "external",
    is_latest: false,
    operations: [],
    skills: [],
    member_count: 9,
    project: null,
    agent: null,
    ...extra,
  };
}

function mountCard(versions: CountedVersion[]): VueWrapper {
  return mount(AbilityPackageCard, {
    props: {
      title: "Core",
      fullName: "ghrah.core",
      namespace: "ghrah",
      name: "core",
      versions: versions as unknown as AbilityPackageGroupVersionView[],
      expandedKeys: new Set<string>(),
    },
  });
}

function meta(wrapper: VueWrapper): string {
  return wrapper.get(".ability-package-meta").text();
}

describe("AbilityPackageCard", () => {
  it("shows tools and skills once both counts are present", () => {
    const wrapper = mountCard([version({ operation_count: 4, skill_count: 1 })]);
    expect(meta(wrapper)).toBe("4 tools · 1 skills");
    wrapper.unmount();
  });

  it("omits a zero side instead of printing it", () => {
    const toolsOnly = mountCard([version({ operation_count: 3, skill_count: 0 })]);
    expect(meta(toolsOnly)).toBe("3 tools");
    toolsOnly.unmount();

    const skillsOnly = mountCard([version({ operation_count: 0, skill_count: 2 })]);
    expect(meta(skillsOnly)).toBe("2 skills");
    skillsOnly.unmount();
  });

  it("falls back to the tool count when both sides are zero", () => {
    const wrapper = mountCard([version({ operation_count: 0, skill_count: 0 })]);
    expect(meta(wrapper)).toBe("0 tools");
    wrapper.unmount();
  });

  it("keeps the member count while the split is absent", () => {
    const wrapper = mountCard([version()]);
    expect(meta(wrapper)).toBe("9 members");
    wrapper.unmount();
  });

  it("says how many versions a multi-version group holds", () => {
    const wrapper = mountCard([
      version({ version: "1.0.0", operation_count: 4, skill_count: 1 }),
      version({ version: "0.9.0", origin: "installed" }),
    ]);
    expect(meta(wrapper)).toBe("2 versions");
    wrapper.unmount();
  });
});
