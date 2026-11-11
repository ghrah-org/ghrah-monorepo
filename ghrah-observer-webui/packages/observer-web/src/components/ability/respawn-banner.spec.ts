// SPDX-FileCopyrightText: 2026 chenxya <chenxya@ghrah.org>
//
// SPDX-License-Identifier: Apache-2.0

// @vitest-environment happy-dom

import { mount } from "@vue/test-utils";
import { describe, expect, it } from "vitest";
import RespawnBanner from "./respawn-banner.vue";

function mountBanner(props: Record<string, unknown> = {}) {
  return mount(RespawnBanner, {
    props: {
      status: "ready",
      conflict: false,
      revision: null,
      ...props,
    },
  });
}

describe("RespawnBanner", () => {
  it("renders nothing for a ready surface", () => {
    const wrapper = mountBanner();
    expect(wrapper.text()).toBe("");
  });

  it("renders the unmaterialized message for an empty runtime snapshot", () => {
    const wrapper = mountBanner({ status: "unmaterialized" });
    expect(wrapper.get(".respawn-note").text()).toContain(
      "The runtime tool surface has not been generated yet",
    );
  });

  it("renders the unobserved message when the host has no snapshot", () => {
    const wrapper = mountBanner({ status: "unobserved" });
    expect(wrapper.get(".respawn-note").text()).toContain("No host observation is available");
  });

  it("renders the restart requirement with the pending diff", () => {
    const wrapper = mountBanner({
      status: "restart",
      pendingAdded: ["write_file"],
      pendingRemoved: ["old_tool"],
    });
    const text = wrapper.get(".respawn-note").text();
    expect(text).toContain("These changes take effect after this agent restarts");
    expect(text).toContain("write_file");
    expect(text).toContain("old_tool");
  });

  it("keeps the conflict alert independent from the surface status", () => {
    const wrapper = mountBanner({ status: "unmaterialized", conflict: true, revision: 7 });
    expect(wrapper.get("[role='alert']").text()).toContain("revision 7");
    expect(wrapper.get("[role='status']").text()).toContain(
      "The runtime tool surface has not been generated yet",
    );
  });
});
