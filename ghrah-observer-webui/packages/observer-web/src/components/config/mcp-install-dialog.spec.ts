// SPDX-FileCopyrightText: 2026 chenxya <chenxya@ghrah.org>
//
// SPDX-License-Identifier: Apache-2.0

// @vitest-environment happy-dom

import { flushPromises, mount } from "@vue/test-utils";
import { createPinia, setActivePinia } from "pinia";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import McpInstallDialog from "./mcp-install-dialog.vue";

function json(body: unknown) {
  return new Response(JSON.stringify(body), {
    status: 200,
    headers: { "content-type": "application/json" },
  });
}

function button(wrapper: ReturnType<typeof mount>, label: string) {
  const found = wrapper.findAll("button").find((item) => item.text().includes(label));
  if (!found) throw new Error(`button not found: ${label}`);
  return found;
}

describe("McpInstallDialog", () => {
  beforeEach(() => setActivePinia(createPinia()));
  afterEach(() => vi.unstubAllGlobals());

  it("connects, chooses a tool, previews members, and installs", async () => {
    const calls: Array<{ path: string; body: unknown }> = [];
    vi.stubGlobal(
      "fetch",
      vi.fn(async (url: string, init?: { method?: string; body?: string }) => {
        const path = new URL(url).pathname;
        const body = init?.body ? JSON.parse(init.body) : undefined;
        calls.push({ path, body });
        if (path.endsWith("/mcp/probe")) {
          return json({
            format: "ghrah.ability-control.mcp-draft-probe.v1",
            ok: true,
            phase: "list_tools",
            elapsed_ms: 12,
            protocol_version: "2025-06-18",
            server_info: { name: "docs-mcp", version: "1.0.0" },
            tool_count: 1,
            tools: [
              {
                name: "search",
                title: "Docs search",
                description: "Search docs",
                input_schema: { type: "object", properties: { query: { type: "string" } } },
                output_schema: null,
                read_only_hint: true,
              },
            ],
            resources: [],
            resource_count: 0,
            resources_supported: false,
            truncated: false,
            error: null,
          });
        }
        return json({
          format: "ghrah.ability-control.mcp-install.v1",
          package: "mcp.docs_mcp",
          version: "0.0.1",
          digest: "sha256:abc",
          members: [
            {
              member: "mcp.docs_mcp.docs_mcp_search",
              type: "mcp",
              server: "docs_mcp_mcp",
              tool: "search",
              read_only: true,
            },
          ],
          member_count: 1,
          version_bumped: false,
          already_installed: false,
          agentconf: { provider_id: "docs_mcp-provider", server_id: "docs_mcp_mcp" },
        });
      }),
    );

    const wrapper = mount(McpInstallDialog, {
      global: { stubs: { teleport: true } },
    });
    await flushPromises();

    await wrapper.get('input[placeholder="e.g. Alibaba Cloud MCP"]').setValue("Docs MCP");
    await wrapper.get('input[placeholder="https://…/mcp"]').setValue("https://example.test/mcp");
    await wrapper.get('input[placeholder="Secret stored in the MCP provider"]').setValue("token");
    await button(wrapper, "Test connection and fetch tools").trigger("click");
    await flushPromises();
    expect(wrapper.text()).toContain("Connection succeeded");
    await button(wrapper, "Next: choose tools").trigger("click");
    await flushPromises();
    expect(wrapper.text()).toContain("Docs search");

    await wrapper.get(".mcp-tool input[type=checkbox]").setValue(true);
    await button(wrapper, "Next: generate Ability").trigger("click");
    await flushPromises();
    expect(wrapper.text()).toContain("mcp.docs_mcp.docs_mcp_search");

    await button(wrapper, "Generate Ability").trigger("click");
    await flushPromises();
    expect(wrapper.text()).toContain("Ability generated");
    expect(wrapper.emitted("installed")).toHaveLength(1);

    const install = calls.find((call) => call.path.endsWith("/mcp/install"));
    expect(install?.body).toMatchObject({
      server: { ref: "docs_mcp_mcp", display_name: "Docs MCP" },
      tools: ["search"],
      ability: {
        name: "docs_mcp",
        version: "0.0.1",
        prefix: "docs_mcp_",
        title: "Docs MCP",
      },
    });
  });

  it("reuses an existing MCP provider without asking for a secret", async () => {
    const calls: Array<{ path: string; body: unknown }> = [];
    vi.stubGlobal(
      "fetch",
      vi.fn(async (url: string, init?: { method?: string; body?: string }) => {
        const path = new URL(url).pathname;
        const body = init?.body ? JSON.parse(init.body) : undefined;
        calls.push({ path, body });
        if (path.endsWith("/mcp-providers")) {
          return json({
            format: "ghrah.agentconf-control.mcp-providers.v1",
            providers: [
              {
                format: "ghrah.agentconf-control.mcp-provider.v1",
                id: "provider-1",
                name: "shared-provider",
                vendor: "Vendor",
                api_key_configured: true,
                default_binding: null,
                revision: 1,
                created_at: "2026-11-24T00:00:00+00:00",
                updated_at: "2026-11-24T00:00:00+00:00",
              },
            ],
          });
        }
        return json({
          format: "ghrah.ability-control.mcp-draft-probe.v1",
          ok: true,
          phase: "list_tools",
          elapsed_ms: 5,
          tool_count: 1,
          tools: [
            {
              name: "echo",
              title: "Echo",
              description: null,
              input_schema: { type: "object" },
              output_schema: null,
              read_only_hint: true,
            },
          ],
          resources: [],
          resource_count: 0,
          resources_supported: false,
          truncated: false,
          error: null,
        });
      }),
    );
    const wrapper = mount(McpInstallDialog, {
      global: { stubs: { teleport: true } },
    });
    await flushPromises();
    expect(wrapper.text()).toContain("shared-provider");
    expect(wrapper.find('input[placeholder="Secret stored in the MCP provider"]').exists()).toBe(
      false,
    );
    await wrapper.get('input[placeholder="e.g. Alibaba Cloud MCP"]').setValue("Shared");
    await wrapper.get('input[placeholder="https://…/mcp"]').setValue("https://example.test/mcp");
    await button(wrapper, "Test connection and fetch tools").trigger("click");
    await flushPromises();
    const probe = calls.find((call) => call.path.endsWith("/mcp/probe"));
    expect(probe?.body).toMatchObject({
      auth: { mode: "provider", provider_id: "provider-1", key: "" },
    });
  });

  it("allows selecting a provider without a secret", async () => {
    const calls: Array<{ path: string; body: unknown }> = [];
    vi.stubGlobal(
      "fetch",
      vi.fn(async (url: string, init?: { method?: string; body?: string }) => {
        const path = new URL(url).pathname;
        const body = init?.body ? JSON.parse(init.body) : undefined;
        calls.push({ path, body });
        if (path.endsWith("/mcp-providers")) {
          return json({
            format: "ghrah.agentconf-control.mcp-providers.v1",
            providers: [
              {
                format: "ghrah.agentconf-control.mcp-provider.v1",
                id: "provider-keyless",
                name: "keyless-provider",
                vendor: "Local",
                api_key_configured: false,
                default_binding: null,
                revision: 1,
                created_at: "2026-11-24T00:00:00+00:00",
                updated_at: "2026-11-24T00:00:00+00:00",
              },
            ],
          });
        }
        return json({
          format: "ghrah.ability-control.mcp-draft-probe.v1",
          ok: true,
          phase: "list_tools",
          elapsed_ms: 5,
          tool_count: 1,
          tools: [
            {
              name: "echo",
              title: "Echo",
              description: null,
              input_schema: { type: "object" },
              output_schema: null,
              read_only_hint: true,
            },
          ],
          resources: [],
          resource_count: 0,
          resources_supported: false,
          truncated: false,
          error: null,
        });
      }),
    );
    const wrapper = mount(McpInstallDialog, {
      global: { stubs: { teleport: true } },
    });
    await flushPromises();
    const option = wrapper
      .findAll("option")
      .find((item) => item.text().includes("keyless-provider"));
    expect(option?.attributes("disabled")).toBeUndefined();
    expect(wrapper.text()).toContain("no secret");
    expect(wrapper.find('input[placeholder="Secret stored in the MCP provider"]').exists()).toBe(
      false,
    );
    await wrapper.get('input[placeholder="e.g. Alibaba Cloud MCP"]').setValue("Keyless");
    await wrapper.get('input[placeholder="https://…/mcp"]').setValue("https://example.test/mcp");
    await button(wrapper, "Test connection and fetch tools").trigger("click");
    await flushPromises();
    const probe = calls.find((call) => call.path.endsWith("/mcp/probe"));
    expect(probe?.body).toMatchObject({
      auth: { mode: "provider", provider_id: "provider-keyless", key: "" },
    });
  });

  it("keeps the next step disabled when the probe returns no tools", async () => {
    vi.stubGlobal(
      "fetch",
      vi.fn(async () =>
        json({
          format: "ghrah.ability-control.mcp-draft-probe.v1",
          ok: true,
          phase: "list_tools",
          elapsed_ms: 8,
          tool_count: 0,
          tools: [],
          resources: [],
          resource_count: 0,
          resources_supported: false,
          truncated: false,
          error: null,
        }),
      ),
    );
    const wrapper = mount(McpInstallDialog, {
      global: { stubs: { teleport: true } },
    });
    await flushPromises();
    await wrapper.get('input[placeholder="e.g. Alibaba Cloud MCP"]').setValue("Empty");
    await wrapper.get('input[placeholder="https://…/mcp"]').setValue("https://example.test/mcp");
    await wrapper.get('input[placeholder="Secret stored in the MCP provider"]').setValue("token");
    await button(wrapper, "Test connection and fetch tools").trigger("click");
    await flushPromises();
    expect(button(wrapper, "Next: choose tools").attributes("disabled")).toBeDefined();
    expect(wrapper.text()).toContain("returned no usable tools");
  });
});
