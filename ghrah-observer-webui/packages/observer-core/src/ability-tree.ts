// SPDX-FileCopyrightText: 2026 chenxya <chenxya@ghrah.org>
//
// SPDX-License-Identifier: Apache-2.0

import {
  AbilityTreeIdSchema,
  ContentReadRequestSchema,
  ContentReadResultSchema,
  type ContentReadResult,
  AbilityTreeViewSchema,
  BindToolRequestSchema,
  GrantAbilityRequestSchema,
  RevokeAbilityRequestSchema,
  TreeChangeResultSchema,
  UnbindToolRequestSchema,
  generateRequestId,
  type AbilityTreeView,
  type BindToolRequest,
  type GrantAbilityRequest,
  type RevokeAbilityRequest,
  type TreeChangeResult,
  type UnbindToolRequest,
} from "@ghrah/protocol";

import type { AbilityControlClient } from "./ability-control.js";

/** 由宿主注入受信连接；页面不能在载荷中自报执行主体。 */
export interface AbilityTreeTransport {
  read(path: string): Promise<unknown>;
  submit(
    path: string,
    action: "bind_tool" | "unbind_tool" | "grant" | "revoke" | "read",
    arguments_: unknown,
    requestKey: string,
  ): Promise<unknown>;
}

export interface AbilityTreeClient {
  read(agentId: string): Promise<AbilityTreeView>;
  readContent(
    agentId: string,
    alias: string,
    member: string,
    version: string,
    offset?: number,
  ): Promise<ContentReadResult>;
  bindTool(agentId: string, request: BindToolRequest): Promise<TreeChangeResult>;
  unbindTool(agentId: string, request: UnbindToolRequest): Promise<TreeChangeResult>;
  grantAbility(agentId: string, request: GrantAbilityRequest): Promise<TreeChangeResult>;
  revokeAbility(agentId: string, request: RevokeAbilityRequest): Promise<TreeChangeResult>;
}

/** 同源投影消费，不保存独立授权或执行旁路，也不自动重试变更。 */
export function createAbilityTreeClient(
  projectId: string,
  transport: AbilityTreeTransport,
): AbilityTreeClient {
  AbilityTreeIdSchema.parse(projectId);
  function view(value: unknown, agentId: string): AbilityTreeView {
    const parsed = AbilityTreeViewSchema.parse(value);
    if (parsed.project_id !== projectId || parsed.agent_id !== agentId) {
      throw new Error("Ability tree target mismatch");
    }
    return parsed;
  }
  async function change(
    agentId: string,
    action: "bind_tool" | "unbind_tool" | "grant" | "revoke",
    request: unknown,
  ) {
    const target = AbilityTreeIdSchema.parse(agentId);
    const result = TreeChangeResultSchema.parse(
      await transport.submit(`/scheme/agent/${target}`, action, request, generateRequestId()),
    );
    if (result.view !== null) view(result.view, target);
    return result;
  }
  return {
    async read(agentId) {
      const target = AbilityTreeIdSchema.parse(agentId);
      return view(await transport.read(`/scheme/view/${target}`), target);
    },
    async readContent(agentId, alias, member, version, offset = 0) {
      const target = AbilityTreeIdSchema.parse(agentId);
      if (
        !/^[a-z][a-z0-9_-]{0,63}$/.test(alias) ||
        !/^skill\/[a-z][a-z0-9_-]{0,63}(\/(assets|references|scripts)\/[a-z][a-z0-9_-]{0,63})?$/.test(
          member,
        )
      ) {
        throw new Error("Invalid content member");
      }
      const result = ContentReadResultSchema.parse(
        await transport.submit(
          `/scheme/agent/${target}/ability/${alias}/${member}`,
          "read",
          ContentReadRequestSchema.parse({ offset }),
          generateRequestId(),
        ),
      );
      if (result.member !== member || result.version_digest !== version) {
        throw new Error("Content target mismatch");
      }
      return result;
    },
    bindTool: (agentId, request) =>
      change(agentId, "bind_tool", BindToolRequestSchema.parse(request)),
    unbindTool: (agentId, request) =>
      change(agentId, "unbind_tool", UnbindToolRequestSchema.parse(request)),
    grantAbility: (agentId, request) =>
      change(agentId, "grant", GrantAbilityRequestSchema.parse(request)),
    revokeAbility: (agentId, request) =>
      change(agentId, "revoke", RevokeAbilityRequestSchema.parse(request)),
  };
}

const VIEW_PATH = /^\/scheme\/view\/([^/]+)$/;
const AGENT_PATH = /^\/scheme\/agent\/([^/]+)$/;
const CONTENT_PATH = /^\/scheme\/agent\/([^/]+)\/ability\/([^/]+)\/(.+)$/;

/** REST 管理面的 Scheme transport 适配：路径与 action 保持一致，语义仍归领域。 */
export function createRestAbilityTreeTransport(
  client: AbilityControlClient,
  projectId: string,
): AbilityTreeTransport {
  AbilityTreeIdSchema.parse(projectId);
  return {
    async read(path) {
      const match = VIEW_PATH.exec(path);
      if (!match) throw new Error("Unsupported ability tree read path");
      return client.tree(match[1]);
    },
    async submit(path, action, arguments_, requestKey) {
      void requestKey;
      const content = CONTENT_PATH.exec(path);
      if (action === "read") {
        if (!content) throw new Error("Unsupported ability content path");
        const [, agentId, alias, member] = content;
        return client.content(agentId, alias, member, contentOffset(arguments_));
      }
      const match = AGENT_PATH.exec(path);
      if (!match) throw new Error("Unsupported ability tree change path");
      const agentId = match[1];
      const result = await changeForAction(client, agentId, action, arguments_);
      return { view: result.view, error: null };
    },
  };
}

async function changeForAction(
  client: AbilityControlClient,
  agentId: string,
  action: "bind_tool" | "unbind_tool" | "grant" | "revoke",
  arguments_: unknown,
) {
  switch (action) {
    case "grant":
      return client.putGrant(agentId, arguments_ as GrantAbilityRequest);
    case "revoke":
      return client.revokeGrant(agentId, arguments_ as RevokeAbilityRequest);
    case "bind_tool":
      return client.bindTool(agentId, arguments_ as BindToolRequest);
    case "unbind_tool":
      return client.unbindTool(agentId, arguments_ as UnbindToolRequest);
  }
}

function contentOffset(arguments_: unknown): number {
  const parsed = ContentReadRequestSchema.safeParse(arguments_);
  return parsed.success ? parsed.data.offset : 0;
}
