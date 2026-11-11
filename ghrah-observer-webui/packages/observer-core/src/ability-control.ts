import {
  AbilityAdminViewSchema,
  AbilityApiStatusSchema,
  AbilityChangesPageSchema,
  AbilityPackageGroupsViewSchema,
  AbilityTreeChangeResultSchema,
  AbilityTreeIdSchema,
  AbilityTreeViewSchema,
  BindToolRequestSchema,
  ContentReadResultSchema,
  GrantAbilityRequestSchema,
  type AbilityAdminView,
  type AbilityApiStatus,
  type AbilityChangesPage,
  type AbilityPackageGroupsView,
  type AbilityTreeChangeResult,
  type AbilityTreeView,
  type BindToolRequest,
  type ContentReadResult,
  type GrantAbilityRequest,
  type RevokeAbilityRequest,
  RevokeAbilityRequestSchema,
  type UnbindToolRequest,
  UnbindToolRequestSchema,
} from "@ghrah/protocol";

import { HttpClient, HttpError } from "./http.js";

export interface AbilityControlClientOptions {
  /** 项目作用域；所有端点都从受信路径取项目，不在载荷里自报。 */
  projectId: string;
}

export interface AbilityAdminQuery {
  /** 一并读取的审计条数（服务端有界）。 */
  changesLimit?: number;
}

export interface AbilityChangesQuery {
  afterSeq?: number;
  limit?: number;
}

/** 管理面 CAS 冲突；携带服务端返回的最新 revision 与视图，供 UI 重读而非重试。 */
export class AbilityConflictError extends HttpError {
  constructor(error: HttpError) {
    super({
      code: "conflict",
      status: 409,
      message: error.message,
      detail: error.detail,
    });
    this.name = "AbilityConflictError";
  }

  get revision(): number | undefined {
    const detail = this.detail as { revision?: unknown } | undefined;
    return typeof detail?.revision === "number" ? detail.revision : undefined;
  }

  get view(): AbilityTreeView | undefined {
    const detail = this.detail as { view?: unknown } | undefined;
    const parsed = AbilityTreeViewSchema.safeParse(detail?.view);
    return parsed.success ? parsed.data : undefined;
  }
}

export interface AbilityControlClient {
  status(): Promise<AbilityApiStatus>;
  packageGroups(): Promise<AbilityPackageGroupsView>;
  projectPackageGroups(): Promise<AbilityPackageGroupsView>;
  agentPackageGroups(agentId: string): Promise<AbilityPackageGroupsView>;
  tree(agentId: string): Promise<AbilityTreeView>;
  admin(agentId: string, query?: AbilityAdminQuery): Promise<AbilityAdminView>;
  changes(agentId: string, query?: AbilityChangesQuery): Promise<AbilityChangesPage>;
  content(
    agentId: string,
    alias: string,
    member: string,
    offset?: number,
  ): Promise<ContentReadResult>;
  putGrant(agentId: string, request: GrantAbilityRequest): Promise<AbilityTreeChangeResult>;
  revokeGrant(agentId: string, request: RevokeAbilityRequest): Promise<AbilityTreeChangeResult>;
  bindTool(agentId: string, request: BindToolRequest): Promise<AbilityTreeChangeResult>;
  unbindTool(agentId: string, request: UnbindToolRequest): Promise<AbilityTreeChangeResult>;
}

function rejectTarget(actual: AbilityTreeView, projectId: string, agentId: string): void {
  if (actual.project_id !== projectId || actual.agent_id !== agentId) {
    throw new Error("Ability control target mismatch");
  }
}

async function convertConflict<T>(action: () => Promise<T>): Promise<T> {
  try {
    return await action();
  } catch (error) {
    if (error instanceof HttpError && error.status === 409) {
      throw new AbilityConflictError(error);
    }
    throw error;
  }
}

/** 管理面客户端：只做传输与契约校验，不缓存授权结论、不自动重试写入。 */
export function createAbilityControlClient(
  http: HttpClient,
  options: AbilityControlClientOptions,
): AbilityControlClient {
  const projectId = AbilityTreeIdSchema.parse(options.projectId);
  const base = `/api/v1/projects/${projectId}/agents`;

  function agentPath(agentId: string, suffix = ""): string {
    const target = AbilityTreeIdSchema.parse(agentId);
    return `${base}/${target}${suffix}`;
  }

  async function tree(agentId: string): Promise<AbilityTreeView> {
    const view = AbilityTreeViewSchema.parse(
      await http.get<unknown>(agentPath(agentId, "/ability-tree")),
    );
    rejectTarget(view, projectId, agentId);
    return view;
  }

  function rejectGroups(
    view: AbilityPackageGroupsView,
    scope: AbilityPackageGroupsView["scope"],
    agentId?: string,
  ): void {
    const mismatched =
      view.scope !== scope ||
      (scope === "distribution" && (view.project_id !== null || view.agent_id !== null)) ||
      (scope !== "distribution" && view.project_id !== projectId) ||
      (scope === "project" && view.agent_id !== null) ||
      (scope === "agent" && view.agent_id !== agentId);
    if (mismatched) throw new Error("Ability package group target mismatch");
  }

  async function readGroups(
    path: string,
    scope: AbilityPackageGroupsView["scope"],
    agentId?: string,
  ): Promise<AbilityPackageGroupsView> {
    const view = AbilityPackageGroupsViewSchema.parse(await http.get<unknown>(path));
    rejectGroups(view, scope, agentId);
    return view;
  }

  return {
    async status() {
      return AbilityApiStatusSchema.parse(await http.get<unknown>("/api/v1/abilities/status"));
    },
    async packageGroups() {
      return readGroups("/api/v1/ability-package-groups", "distribution");
    },
    async projectPackageGroups() {
      return readGroups(`/api/v1/projects/${projectId}/ability-package-groups`, "project");
    },
    async agentPackageGroups(agentId) {
      return readGroups(agentPath(agentId, "/ability-package-groups"), "agent", agentId);
    },
    tree,
    async admin(agentId, query = {}) {
      const view = AbilityAdminViewSchema.parse(
        await http.get<unknown>(agentPath(agentId, "/ability-admin"), {
          query: { changes_limit: query.changesLimit },
        }),
      );
      if (view.project_id !== projectId || view.agent_id !== agentId) {
        throw new Error("Ability admin target mismatch");
      }
      return view;
    },
    async changes(agentId, query = {}) {
      const page = AbilityChangesPageSchema.parse(
        await http.get<unknown>(agentPath(agentId, "/ability-changes"), {
          query: { after_seq: query.afterSeq, limit: query.limit },
        }),
      );
      return page;
    },
    async content(agentId, alias, member, offset = 0) {
      if (
        !/^[a-z][a-z0-9_-]{0,63}$/.test(alias) ||
        !/^skill\/[a-z][a-z0-9_-]{0,63}(\/(assets|references|scripts)\/[a-z][a-z0-9_-]{0,63})?$/.test(
          member,
        )
      ) {
        throw new Error("Invalid content member");
      }
      return ContentReadResultSchema.parse(
        await http.get<unknown>(
          agentPath(agentId, `/ability-tree/abilities/${alias}/content/${member}`),
          {
            query: { offset },
          },
        ),
      );
    },
    async putGrant(agentId, request) {
      const parsed = GrantAbilityRequestSchema.parse(request);
      return convertConflict(async () => {
        const result = AbilityTreeChangeResultSchema.parse(
          await http.put<unknown>(
            agentPath(agentId, `/ability-tree/grants/${parsed.alias}`),
            parsed,
          ),
        );
        if (result.view !== null) rejectTarget(result.view, projectId, agentId);
        return result;
      });
    },
    async revokeGrant(agentId, request) {
      const parsed = RevokeAbilityRequestSchema.parse(request);
      return convertConflict(async () => {
        const result = AbilityTreeChangeResultSchema.parse(
          await http.delete<unknown>(agentPath(agentId, `/ability-tree/grants/${parsed.alias}`), {
            query: {
              expected_revision: parsed.expected_revision,
              members: parsed.members ?? undefined,
            },
          }),
        );
        if (result.view !== null) rejectTarget(result.view, projectId, agentId);
        return result;
      });
    },
    async bindTool(agentId, request) {
      const parsed = BindToolRequestSchema.parse(request);
      return convertConflict(async () => {
        const result = AbilityTreeChangeResultSchema.parse(
          await http.put<unknown>(
            agentPath(agentId, `/ability-tree/tools/${parsed.tool_name}`),
            parsed,
          ),
        );
        if (result.view !== null) rejectTarget(result.view, projectId, agentId);
        return result;
      });
    },
    async unbindTool(agentId, request) {
      const parsed = UnbindToolRequestSchema.parse(request);
      return convertConflict(async () => {
        const result = AbilityTreeChangeResultSchema.parse(
          await http.delete<unknown>(
            agentPath(agentId, `/ability-tree/tools/${parsed.tool_name}`),
            {
              query: { expected_revision: parsed.expected_revision },
            },
          ),
        );
        if (result.view !== null) rejectTarget(result.view, projectId, agentId);
        return result;
      });
    },
  };
}
