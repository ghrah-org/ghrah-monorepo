import { expect, it } from "vitest";
import fixture from "./__fixtures__/workspace-detail.json";
import { WorkspaceGetResponsePayloadSchema } from "./payloads/workspace.js";
it("keeps false provider state and null diagnostics from the Python response", () => {
  expect(WorkspaceGetResponsePayloadSchema.parse(fixture)).toEqual(fixture);
  expect(WorkspaceGetResponsePayloadSchema.safeParse({ ...fixture, exists: "false" }).success).toBe(
    false,
  );
});
