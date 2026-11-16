import {
  AbilityAdminViewSchema,
  AbilityApiStatusSchema,
  AbilityChangesPageSchema,
  AbilityMcpProbeRequestSchema,
  AbilityMcpProbeViewSchema,
  AbilityPackageGroupsViewSchema,
  AbilityPackageVersionProfileViewSchema,
  AbilityTreeChangeResultSchema,
  AbilityTreeIdSchema,
  AbilityTreeViewSchema,
  BindToolRequestSchema,
  ContentReadResultSchema,
  GrantAbilityRequestSchema,
  type AbilityAdminView,
  type AbilityApiStatus,
  type AbilityChangesPage,
  type AbilityMcpProbeView,
  type AbilityPackageGroupsView,
  type AbilityPackageVersionProfileView,
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

import { HttpClient, HttpError, type HttpAbortSignal } from "./http.js";

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

/** 一次只读 MCP 探测的调用参数。超时与资源开关由契约夹逼，不在这里改写。 */
export interface AbilityMcpProbeOptions {
  /** 服务端探测预算，毫秒。缺省 5000，合法范围 1000–10000。 */
  timeoutMs?: number;
  /** 为 true 时额外查询资源清单。缺省 false。 */
  includeResources?: boolean;
  /** 调用方取消。中止后 store 不把这次传输失败写成探测报告。 */
  signal?: HttpAbortSignal;
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
  packageVersion(
    namespace: string,
    name: string,
    version: string,
  ): Promise<AbilityPackageVersionProfileView>;
  probePackageMcp(
    namespace: string,
    name: string,
    version: string,
    options?: AbilityMcpProbeOptions,
  ): Promise<AbilityMcpProbeView>;
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

/** 与 Subject 路径身份一致：小写开头，不含点号与大写。 */
const PACKAGE_IDENTITY = /^[a-z][a-z0-9_-]*$/;
/** SemVer core 三段，与 Core `V1_SEMVER_PATTERN` 一致。 */
const PACKAGE_VERSION = /^(0|[1-9][0-9]{0,8})\.(0|[1-9][0-9]{0,8})\.(0|[1-9][0-9]{0,8})$/;

function packageVersionPath(namespace: string, name: string, version: string): string {
  if (!PACKAGE_IDENTITY.test(namespace) || !PACKAGE_IDENTITY.test(name)) {
    throw new Error("Invalid package identity");
  }
  if (!PACKAGE_VERSION.test(version)) {
    throw new Error("Invalid package version");
  }
  const ns = encodeURIComponent(namespace);
  const pkg = encodeURIComponent(name);
  const ver = encodeURIComponent(version);
  return `/api/v1/ability-packages/${ns}/${pkg}/versions/${ver}`;
}

function rejectPackageVersion(
  view: AbilityPackageVersionProfileView,
  namespace: string,
  name: string,
  version: string,
): void {
  if (
    view.namespace !== namespace ||
    view.name !== name ||
    view.version !== version ||
    view.full_name !== `${namespace}.${name}`
  ) {
    throw new Error("Ability package version target mismatch");
  }
}

function rejectProbeTarget(
  view: AbilityMcpProbeView,
  namespace: string,
  name: string,
  version: string,
): void {
  if (view.namespace !== namespace || view.name !== name || view.version !== version) {
    throw new Error("Ability MCP probe target mismatch");
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
    async packageVersion(namespace, name, version) {
      const view = AbilityPackageVersionProfileViewSchema.parse(
        await http.get<unknown>(packageVersionPath(namespace, name, version)),
      );
      rejectPackageVersion(view, namespace, name, version);
      return view;
    },
    async probePackageMcp(namespace, name, version, options = {}) {
      const body = AbilityMcpProbeRequestSchema.parse({
        ...(options.timeoutMs === undefined ? {} : { timeout_ms: options.timeoutMs }),
        ...(options.includeResources === undefined
          ? {}
          : { include_resources: options.includeResources }),
      });
      const view = AbilityMcpProbeViewSchema.parse(
        await http.post<unknown>(
          `${packageVersionPath(namespace, name, version)}/mcp/probe`,
          body,
          {
            timeoutMs: body.timeout_ms + 3000,
            signal: options.signal,
          },
        ),
      );
      rejectProbeTarget(view, namespace, name, version);
      return view;
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
