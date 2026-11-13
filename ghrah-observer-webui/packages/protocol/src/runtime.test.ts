import { readFileSync } from "node:fs";
import { describe, expect, it } from "vitest";
import type { ZodType } from "zod";
import * as schemas from "./runtime.generated.js";

const contracts = "../../../../ghrah-protocol/contracts/runtime/";
const fixture = (name: string): unknown =>
  JSON.parse(readFileSync(new URL(contracts + name, import.meta.url), "utf8"));

interface ContractCase {
  name: string;
  schema: string;
  valid: boolean;
  body: unknown;
}

const cases = fixture("schema-cases.json") as ContractCase[];

describe("Runtime resource contract", () => {
  it.each(cases)("$name", ({ schema, valid, body }) => {
    const validator = schemas[(schema + "Schema") as keyof typeof schemas] as unknown;
    expect(validator, schema).toBeDefined();
    expect((validator as ZodType).safeParse(body).success).toBe(valid);
  });

  it("accepts the transitional command fixture and rejects undeclared commands", () => {
    expect(schemas.RuntimeCommandRequestSchema.parse(fixture("command.list.json")).command).toBe(
      "list_agents",
    );
    expect(() =>
      schemas.RuntimeCommandRequestSchema.parse({ command: "execute_arbitrary", payload: {} }),
    ).toThrow();
  });

  it("ignores additive fields even inside capability responses", () => {
    const parsed = schemas.RuntimeMetaSchema.parse({
      contract: "v1",
      capabilities: [{ name: "runtime.lifecycle", version: 1, future: true }],
      server_time: "2026-11-12T00:00:00.000Z",
      future: true,
    });
    expect(parsed).not.toHaveProperty("future");
    expect(parsed.capabilities[0]).not.toHaveProperty("future");
  });

  it("rejects unknown principal fields", () => {
    expect(() =>
      schemas.RuntimePrincipalSchema.parse({ scope_ref: "s", agent_id: "forged" }),
    ).toThrow();
  });

  it("enforces message UTF-8 quota without truncation", () => {
    const text = "界".repeat(Math.floor((256 * 1024) / 3)) + "a";
    expect(new TextEncoder().encode(text).length).toBe(256 * 1024);
    expect(schemas.RuntimeMessageCreateSchema.parse({ text, kind: "user" }).text).toBe(text);
    expect(() =>
      schemas.RuntimeMessageCreateSchema.parse({ text: text + "a", kind: "user" }),
    ).toThrow();
  });

  it("enforces metadata byte quota and rejects non-JSON numbers", () => {
    const metadata = { x: "界".repeat(1362) + "aa" };
    expect(new TextEncoder().encode(JSON.stringify(metadata)).length).toBe(4096);
    expect(
      schemas.RuntimeMessageCreateSchema.safeParse({ text: "ok", kind: "user", metadata }).success,
    ).toBe(true);
    for (const value of [{ x: metadata.x + "a" }, { x: NaN }, { x: undefined }]) {
      expect(
        schemas.RuntimeMessageCreateSchema.safeParse({ text: "ok", kind: "user", metadata: value })
          .success,
      ).toBe(false);
    }
  });
});
