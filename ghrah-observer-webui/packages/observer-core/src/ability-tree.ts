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
  TreeChangeResultSchema,
  UnbindToolRequestSchema,
  generateRequestId,
  type AbilityTreeView,
  type BindToolRequest,
  type TreeChangeResult,
  type UnbindToolRequest,
} from "@ghrah/protocol";

/** 由宿主注入受信连接；页面不能在载荷中自报执行主体。 */
export interface AbilityTreeTransport {
  read(path: string): Promise<unknown>;
  submit(
    path: string,
    action: "bind_tool" | "unbind_tool" | "read",
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
  async function change(agentId: string, action: "bind_tool" | "unbind_tool", request: unknown) {
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
        !/^skill\/[a-z][a-z0-9_-]{0,63}(\/(assets|references)\/[a-z][a-z0-9_-]{0,63})?$/.test(
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
  };
}
