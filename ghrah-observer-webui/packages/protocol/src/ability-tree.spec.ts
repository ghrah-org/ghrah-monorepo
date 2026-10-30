import { describe, expect, it } from "vitest";
import fixture from "./__fixtures__/ability-tree.json";
import {
  AbilityTreeViewSchema,
  BindToolRequestSchema,
  TreeChangeResultSchema,
} from "./ability-tree.js";

describe("Ability resource tree contract", () => {
  it("accepts Python-generated empty, selected, revoked and unavailable projections", () => {
    for (const value of [fixture.initial, fixture.selected, fixture.revoked, fixture.unavailable]) {
      expect(AbilityTreeViewSchema.parse(value)).toEqual(value);
    }
    expect(TreeChangeResultSchema.parse(fixture.bound)).toEqual(fixture.bound);
    expect(TreeChangeResultSchema.parse(fixture.conflict)).toEqual(fixture.conflict);
  });
  it("rejects ambiguous results, coercion and self-reported authority", () => {
    expect(TreeChangeResultSchema.safeParse({ view: null, error: null }).success).toBe(false);
    expect(
      TreeChangeResultSchema.safeParse({ view: fixture.initial, error: "conflict" }).success,
    ).toBe(false);
    const request = {
      alias: "files",
      member: "read_file",
      tool_name: "read_report",
      expected_revision: 1,
    };
    expect(BindToolRequestSchema.safeParse({ ...request, expected_revision: true }).success).toBe(
      false,
    );
    expect(BindToolRequestSchema.safeParse({ ...request, principal: "admin" }).success).toBe(false);
    expect(AbilityTreeViewSchema.safeParse({ ...fixture.initial, unknown: 1 }).success).toBe(false);
  });
});

it.each(["ability", "tool", "operation"])("rejects duplicate %s nodes", (duplicate) => {
  const value = structuredClone(fixture.selected);
  if (duplicate === "ability") value.abilities.push(value.abilities[0]!);
  else if (duplicate === "tool") value.bound_tools.push(value.bound_tools[0]!);
  else {
    const operations = value.abilities[0]!.operations;
    operations.push(operations[0]!);
  }
  expect(AbilityTreeViewSchema.safeParse(value).success).toBe(false);
});
