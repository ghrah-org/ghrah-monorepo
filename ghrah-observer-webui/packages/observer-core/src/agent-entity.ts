import {
  type AgentEntityOperation,
  type AgentIdentity,
  type AgentSnapshot,
  AgentEntityPayloadSchema,
  AgentSnapshotSchema,
  ClientType,
  CommandType,
  type CommandResultPayload,
  type ServerMessage,
} from "@ghrah/protocol";

export interface AgentEntityTransport {
  request(message: ServerMessage): Promise<CommandResultPayload>;
}

/** Stable identity handle. Each operation resolves the current incarnation on the server. */
export class AgentEntity {
  readonly identity: Readonly<AgentIdentity>;
  constructor(
    private readonly transport: AgentEntityTransport,
    projectId: string,
    agentId: string,
  ) {
    this.identity = Object.freeze({ project_id: projectId, agent_id: agentId });
  }

  private async call(operation: AgentEntityOperation): Promise<Record<string, unknown>> {
    const payload = AgentEntityPayloadSchema.parse({ ...this.identity, operation });
    const result = await this.transport.request({
      type: CommandType.AGENT_ENTITY,
      client_type: ClientType.OBSERVER,
      payload,
    });
    if (!result.success)
      throw new Error(
        [result.error ?? "agent_entity_failed", result.error_detail].filter(Boolean).join(": "),
      );
    return (result.data ?? {}) as Record<string, unknown>;
  }

  async snapshot(): Promise<AgentSnapshot> {
    return AgentSnapshotSchema.parse(await this.call({ type: "snapshot" }));
  }
  async start(expectedRevision?: number): Promise<AgentSnapshot> {
    return AgentSnapshotSchema.parse(
      await this.call({ type: "start", expected_revision: expectedRevision }),
    );
  }
  async stop(expectedRevision?: number, expectedIncarnationId?: string): Promise<AgentSnapshot> {
    return AgentSnapshotSchema.parse(
      await this.call({
        type: "stop",
        expected_revision: expectedRevision,
        expected_incarnation_id: expectedIncarnationId,
      }),
    );
  }
  readonly context = {
    listSessions: () => this.call({ type: "context.list_sessions" }),
    getHistory: (sessionId: string, branchId: string, limit = 100) =>
      this.call({
        type: "context.get_history",
        session_id: sessionId,
        branch_id: branchId,
        limit,
      }),
    createSession: (name?: string, systemPrompt?: string) =>
      this.call({
        type: "context.create_session",
        name,
        system_prompt: systemPrompt,
      }),
    activateSession: (sessionId: string) =>
      this.call({ type: "context.activate_session", session_id: sessionId }),
  };
  readonly mailbox = {
    listInbox: (limit = 50, beforeSeq?: number) =>
      this.call({ type: "mailbox.list_inbox", limit, before_seq: beforeSeq }),
    listOutbox: (limit = 50, beforeSeq?: number) =>
      this.call({ type: "mailbox.list_outbox", limit, before_seq: beforeSeq }),
    getMessage: (messageId: string) =>
      this.call({ type: "mailbox.get_message", message_id: messageId }),
    retry: (deliveryId: string) => this.call({ type: "mailbox.retry", delivery_id: deliveryId }),
    resume: (deliveryId: string) => this.call({ type: "mailbox.resume", delivery_id: deliveryId }),
  };
  readonly abilities = {
    list: () => this.call({ type: "abilities.list" }),
    configure: (abilityTypes: string[], expectedRevision: number) =>
      this.call({
        type: "abilities.configure",
        ability_types: abilityTypes,
        expected_revision: expectedRevision,
      }),
  };
}
