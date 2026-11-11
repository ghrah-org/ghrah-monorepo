// SPDX-FileCopyrightText: 2026 chenxya <chenxya@ghrah.org>
//
// SPDX-License-Identifier: Apache-2.0

// @vitest-environment happy-dom

import { flushPromises, mount } from "@vue/test-utils";
import { createPinia, setActivePinia } from "pinia";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import ConnectionsConfigPage from "./connections-config-page.vue";

const provider = {
  format: "ghrah.agentconf-control.provider.v1",
  id: "p1",
  name: "gateway",
  provider_type: "openai",
  base_url: "https://example.test/v1",
  api_key_configured: true,
  connection_options: { timeout: 30, Authorization: "**********" },
  revision: 1,
  created_at: "2026-11-11T00:00:00+00:00",
  updated_at: "2026-11-11T00:00:00+00:00",
};

function json(body: unknown, status = 200) {
  return new Response(JSON.stringify(body), {
    status,
    headers: { "content-type": "application/json" },
  });
}

function error(code: string, status: number) {
  return json({ error: { code, message: code } }, status);
}

describe("ConnectionsConfigPage", () => {
  beforeEach(() => setActivePinia(createPinia()));
  afterEach(() => vi.unstubAllGlobals());

  it("keeps a blank secret and sends masked options back unchanged", async () => {
    const calls: Array<{ method: string; body: unknown }> = [];
    vi.stubGlobal(
      "fetch",
      vi.fn(async (url: string, init?: { method?: string; body?: string }) => {
        const path = new URL(url).pathname;
        const method = init?.method ?? "GET";
        const body = init?.body ? JSON.parse(init.body) : undefined;
        calls.push({ method, body });
        if (path.endsWith("/status")) {
          return json({ format: "ghrah.agentconf-control.status.v1", schema_version: 6 });
        }
        if (path.endsWith("/references")) {
          return json({
            format: "ghrah.agentconf-control.provider-references.v1",
            id: "p1",
            names: ["chat-main"],
          });
        }
        if (method === "PATCH") return json({ ...provider, revision: 2 });
        if (path.endsWith("/providers/p1")) return json(provider);
        return json({
          format: "ghrah.agentconf-control.providers.v1",
          providers: [provider],
        });
      }),
    );
    const wrapper = mount(ConnectionsConfigPage);
    await flushPromises();
    expect(wrapper.text()).toContain("shared by every project");
    expect(wrapper.text()).toContain("gateway");
    await wrapper.get("[data-testid=connection-list] button").trigger("click");
    await flushPromises();
    expect(wrapper.text()).toContain("chat-main");
    expect(wrapper.text()).toContain("A secret is already stored.");
    await wrapper.get("form").trigger("submit");
    await flushPromises();
    const patch = calls.find((call) => call.method === "PATCH");
    expect(patch?.body).toMatchObject({
      expected_revision: 1,
      name: "gateway",
      connection_options: { timeout: 30, Authorization: "**********" },
    });
    expect(patch?.body).not.toHaveProperty("api_key");
    await wrapper.get("button.ui-button-danger").trigger("click");
    expect(wrapper.text()).toContain("This record is still referenced.");
    expect(calls.some((call) => call.method === "DELETE")).toBe(false);
    wrapper.unmount();
  });

  it("explains a database that still needs migration", async () => {
    vi.stubGlobal(
      "fetch",
      vi.fn(async () => error("migration_required", 503)),
    );
    const wrapper = mount(ConnectionsConfigPage);
    await flushPromises();
    expect(wrapper.text()).toContain("agentconf migrate");
    expect(wrapper.find("form").exists()).toBe(false);
    wrapper.unmount();
  });

  it("clears the previous list when the newly selected page fails to load", async () => {
    vi.stubGlobal(
      "fetch",
      vi.fn(async (url: string) => {
        const path = new URL(url).pathname;
        if (path.endsWith("/status")) {
          return json({ format: "ghrah.agentconf-control.status.v1", schema_version: 6 });
        }
        if (path.endsWith("/providers")) {
          return json({ format: "ghrah.agentconf-control.providers.v1", providers: [provider] });
        }
        return error("unavailable", 503);
      }),
    );
    const wrapper = mount(ConnectionsConfigPage);
    await flushPromises();
    expect(wrapper.text()).toContain("gateway");
    await wrapper
      .findAll("[role=tab]")
      .find((tab) => tab.text() === "Model endpoints")!
      .trigger("click");
    await flushPromises();
    expect(wrapper.text()).not.toContain("gateway");
    expect(wrapper.text()).toContain("Nothing here yet.");
    expect(wrapper.get("[role=alert]").text()).toContain("unavailable");
    wrapper.unmount();
  });

  it("stays on the saved record when refreshing the list fails", async () => {
    let saved = false;
    const calls: string[] = [];
    vi.stubGlobal(
      "fetch",
      vi.fn(async (url: string, init?: { method?: string; body?: string }) => {
        const path = new URL(url).pathname;
        const method = init?.method ?? "GET";
        calls.push(method);
        if (path.endsWith("/status")) {
          return json({ format: "ghrah.agentconf-control.status.v1", schema_version: 6 });
        }
        if (method === "POST") {
          saved = true;
          return json({ ...provider, name: "fresh" });
        }
        if (saved && method === "GET" && path.endsWith("/providers"))
          return error("unavailable", 503);
        if (path.endsWith("/references")) {
          return json({
            format: "ghrah.agentconf-control.provider-references.v1",
            id: "p1",
            names: [],
          });
        }
        return json({ format: "ghrah.agentconf-control.providers.v1", providers: [] });
      }),
    );
    const wrapper = mount(ConnectionsConfigPage);
    await flushPromises();
    await wrapper
      .findAll("button")
      .find((button) => button.text() === "New")!
      .trigger("click");
    await wrapper.get("[data-testid=connection-name]").setValue("fresh");
    await wrapper.findAll("input")[2]!.setValue("https://example.test/v1");
    await wrapper.get("form").trigger("submit");
    await flushPromises();
    expect(wrapper.text()).toContain("Saved.");
    expect(wrapper.get("[data-testid=connection-name]").element).toHaveProperty("value", "fresh");
    expect(wrapper.find("[data-testid=connection-list] button").exists()).toBe(false);
    calls.length = 0;
    await wrapper.get("form").trigger("submit");
    await flushPromises();
    expect(calls).toContain("PATCH");
    expect(calls).not.toContain("POST");
    wrapper.unmount();
  });

  it("explains a duplicate name in the interface language", async () => {
    vi.stubGlobal(
      "fetch",
      vi.fn(async (url: string, init?: { method?: string }) => {
        if (new URL(url).pathname.endsWith("/status")) {
          return json({ format: "ghrah.agentconf-control.status.v1", schema_version: 6 });
        }
        if ((init?.method ?? "GET") === "POST") {
          return json(
            { error: { code: "already_exists", message: "资源已存在或违反引用约束" } },
            409,
          );
        }
        return json({ format: "ghrah.agentconf-control.providers.v1", providers: [] });
      }),
    );
    const wrapper = mount(ConnectionsConfigPage);
    await flushPromises();
    await wrapper
      .findAll("button")
      .find((button) => button.text() === "New")!
      .trigger("click");
    await wrapper.get("[data-testid=connection-name]").setValue("gateway");
    await wrapper.findAll("input")[2]!.setValue("https://example.test/v1");
    await wrapper.get("form").trigger("submit");
    await flushPromises();
    expect(wrapper.text()).toContain("A record with this name already exists.");
    expect(wrapper.text()).not.toContain("资源已存在或违反引用约束");
    wrapper.unmount();
  });
});
