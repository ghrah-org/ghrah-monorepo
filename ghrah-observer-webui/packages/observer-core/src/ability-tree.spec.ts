// SPDX-FileCopyrightText: 2026 chenxya <chenxya@ghrah.org>
//
// SPDX-License-Identifier: Apache-2.0

import { describe, expect, it, vi } from "vitest";
import fixture from "../../protocol/src/__fixtures__/ability-tree.json";
import { createAbilityTreeClient, createRestAbilityTreeTransport } from "./ability-tree.js";

describe("AbilityTreeClient", () => {
  it("uses explicit resources and consumes authoritative mutation responses", async () => {
    const transport = {
      read: vi.fn().mockResolvedValue(fixture.initial),
      submit: vi.fn().mockResolvedValue(fixture.bound),
    };
    const client = createAbilityTreeClient("project", transport);
    expect(await client.read("alice")).toEqual(fixture.initial);
    expect(transport.read).toHaveBeenCalledWith("/scheme/view/alice");
    const request = {
      alias: "files",
      member: "read_file",
      tool_name: "read_report",
      expected_revision: 1,
    };
    expect((await client.bindTool("alice", request)).view).toEqual(fixture.selected);
    expect(transport.submit).toHaveBeenCalledWith(
      "/scheme/agent/alice",
      "bind_tool",
      request,
      expect.any(String),
    );
  });
  it("rejects target mismatches, invalid requests and does not retry uncertain submissions", async () => {
    const transport = {
      read: vi.fn().mockResolvedValue(fixture.initial),
      submit: vi.fn().mockRejectedValue(new Error("unknown")),
    };
    const client = createAbilityTreeClient("other-project", transport);
    await expect(client.read("alice")).rejects.toThrow("target mismatch");
    const other = createAbilityTreeClient("project", transport);
    await expect(other.read("bob")).rejects.toThrow("target mismatch");
    await expect(
      other.unbindTool("alice", { tool_name: "read_report", expected_revision: 2 }),
    ).rejects.toThrow("unknown");
    expect(transport.submit).toHaveBeenCalledTimes(1);
  });
});

it("checks content version and member while preserving an explicit read request", async () => {
  const transport = { read: vi.fn(), submit: vi.fn().mockResolvedValue(fixture.text) };
  const client = createAbilityTreeClient("project", transport);
  expect(
    await client.readContent("alice", "reports", "skill/guide", fixture.text.version_digest),
  ).toEqual(fixture.text);
  await expect(
    client.readContent("alice", "reports", "skill/guide", "old-version"),
  ).rejects.toThrow("target mismatch");
  await expect(
    client.readContent("alice", "reports", "skill/guide/../secret", "x"),
  ).rejects.toThrow("Invalid content member");
});

describe("REST transport adapter", () => {
  function controlClient() {
    const result = {
      format: "ghrah.ability-control.change.v1" as const,
      view: fixture.selected,
      revision: 2,
    };
    return {
      status: vi.fn(),
      tree: vi.fn().mockResolvedValue(fixture.initial),
      admin: vi.fn(),
      changes: vi.fn(),
      content: vi.fn().mockResolvedValue(fixture.text),
      putGrant: vi.fn().mockResolvedValue(result),
      revokeGrant: vi.fn().mockResolvedValue(result),
      bindTool: vi.fn().mockResolvedValue(result),
      unbindTool: vi.fn().mockResolvedValue(result),
    };
  }

  it("maps scheme paths and actions to REST resources", async () => {
    const control = controlClient();
    const transport = createRestAbilityTreeTransport(control, "project");
    await expect(transport.read("/scheme/view/alice")).resolves.toEqual(fixture.initial);
    await expect(
      transport.submit("/scheme/agent/alice", "grant", { alias: "files" }, "key"),
    ).resolves.toEqual({ view: fixture.selected, error: null });
    await expect(
      transport.submit("/scheme/agent/alice", "bind_tool", { tool_name: "read_file" }, "key"),
    ).resolves.toEqual({ view: fixture.selected, error: null });
    await expect(
      transport.submit(
        "/scheme/agent/alice/ability/reports/skill/guide",
        "read",
        { offset: 3 },
        "k",
      ),
    ).resolves.toEqual(fixture.text);
    expect(control.content).toHaveBeenCalledWith("alice", "reports", "skill/guide", 3);
    expect(control.putGrant).toHaveBeenCalledWith("alice", { alias: "files" });
  });

  it("rejects unsupported paths instead of guessing a target", async () => {
    const transport = createRestAbilityTreeTransport(controlClient(), "project");
    await expect(transport.read("/scheme/elsewhere/x")).rejects.toThrow("Unsupported");
    await expect(transport.submit("/scheme/agent/alice", "read", {}, "key")).rejects.toThrow(
      "Unsupported",
    );
  });
});
