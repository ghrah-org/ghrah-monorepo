import { expect, it, vi } from "vitest";
import { CommandType, type CommandResultPayload } from "@ghrah/protocol";
import { AgentEntity } from "./agent-entity.js";

it("keeps identity bound across ports and surfaces backend errors", async () => {
  const request = vi.fn().mockResolvedValue({
    success: true,
    data: { items: [] },
  } satisfies Partial<CommandResultPayload>);
  const entity = new AgentEntity({ request }, "p1", "a1");
  await entity.context.listSessions();
  await entity.mailbox.listInbox(20);
  await entity.abilities.list();
  for (const [message] of request.mock.calls) {
    expect(message.type).toBe(CommandType.AGENT_ENTITY);
    expect(message.payload).toMatchObject({ project_id: "p1", agent_id: "a1" });
  }
  expect(request.mock.calls[1]?.[0].payload.operation).toEqual({
    type: "mailbox.list_inbox",
    limit: 20,
    before_seq: null,
  });
  request.mockResolvedValue({
    success: false,
    error: "agent_not_running",
    error_detail: "Start the agent before creating a session",
  });
  await expect(entity.context.createSession("new")).rejects.toThrow(
    "agent_not_running: Start the agent before creating a session",
  );
});
