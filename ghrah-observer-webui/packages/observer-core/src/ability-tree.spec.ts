import { describe, expect, it, vi } from "vitest";
import fixture from "../../protocol/src/__fixtures__/ability-tree.json";
import { createAbilityTreeClient } from "./ability-tree.js";

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
