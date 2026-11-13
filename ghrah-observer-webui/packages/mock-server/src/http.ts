import { createServer, type IncomingMessage, type Server, type ServerResponse } from "node:http";
import { createHash } from "node:crypto";

import {
  AbilityAdminViewSchema,
  AbilityApiStatusSchema,
  AbilityChangesPageSchema,
  AbilityPackageGroupsViewSchema,
  AbilityTreeChangeResultSchema,
  AbilityTreeViewSchema,
  ContentReadResultSchema,
  AgentconfEndpointListViewSchema,
  AgentAdminViewSchema,
  AgentCreateRequestSchema,
  AgentDeletedViewSchema,
  AgentDesiredStateRequestSchema,
  AgentListViewSchema,
  AgentSnapshotSchema,
  CommandType,
  EventType,
  type AbilityPackageGroupsView,
  type AbilityTreeView,
  type MemberAxes,
  type AbilityChangeView,
} from "@ghrah/protocol";
import type { CommandOutcome, MockState } from "./state.js";

export interface MockHttpServerOptions {
  port: number;
  host?: string;
  state?: MockState;
  projectId?: string;
  agentId?: string;
  /** 写端点固定返回 409 冲突，供前端冲突分支联调。 */
  conflictOnWrite?: boolean;
  /** 能力包目录与包组读取；关闭时 status 报 false、包组路由回 503。 */
  packages?: boolean;
  logger?: (line: string) => void;
}

export interface MockHttpServer {
  readonly port: number;
  start(): Promise<void>;
  close(): Promise<void>;
}

const DEFAULT_PROJECT = "project-1";

function defaultTree(projectId: string, agentId: string): AbilityTreeView {
  return AbilityTreeViewSchema.parse({
    format: "ghrah.ability-tree.v2",
    project_id: projectId,
    agent_id: agentId,
    run_id: "run-1",
    revision: 1,
    abilities: [
      {
        alias: "ghrah-fs",
        instance_id: "ghrah-fs",
        instance_revision: 1,
        definition_digest: "sha256:definition",
        version_digest: "sha256:version",
        revision: 1,
        allowed_roots: ["/workspace"],
        operations: [
          {
            key: "read_file",
            description: "Read a file",
            input_schema: { type: "object" },
            output_schema: { type: "object" },
            availability: "ready",
          },
        ],
        skills: [],
        skill: null,
        availability: "ready",
      },
    ],
    bound_tools: [
      {
        name: "read_file",
        alias: "ghrah-fs",
        member: "read_file",
        grant_revision: 1,
        revision: 1,
      },
    ],
    runtime: null,
  });
}

interface AgentRecord {
  tree: AbilityTreeView;
  axes: MemberAxes[];
  requiresRespawn: boolean;
  changes: AbilityChangeView[];
  runtimeIncarnation: string | null;
  runtimeTools: string[] | null;
}

function syncRuntime(record: AgentRecord, incarnation: string | null): void {
  record.tree.run_id = incarnation ?? "";
  if (record.runtimeIncarnation === incarnation) return;
  record.runtimeIncarnation = incarnation;
  record.runtimeTools =
    incarnation === null ? null : record.tree.bound_tools.map((tool) => tool.name);
  if (incarnation !== null) record.requiresRespawn = false;
}

function agentKey(projectId: string, agentId: string): string {
  return `${projectId}:${agentId}`;
}

function defaultAxes(tree: AbilityTreeView): MemberAxes[] {
  return tree.abilities.flatMap((ability) =>
    ability.operations.map((operation) => ({
      member: `operations/${operation.key}`,
      visible: true,
      available: true,
    })),
  );
}

function adminView(record: AgentRecord, running = false) {
  const tree = record.tree;
  return AbilityAdminViewSchema.parse({
    format: "ghrah.ability-control.admin.v1",
    project_id: tree.project_id,
    agent_id: tree.agent_id,
    revision: tree.revision,
    run_id: tree.run_id || null,
    lifecycle: {
      state: running ? "running" : "stopped",
      requires_respawn: record.requiresRespawn,
      pending_reasons: record.requiresRespawn ? ["next start"] : [],
    },
    grant_policy: null,
    grants: tree.abilities.map((ability) => ({
      alias: ability.alias,
      instance_id: ability.instance_id,
      instance_revision: ability.instance_revision,
      version_digest: ability.version_digest,
      definition_digest: ability.definition_digest,
      members: ability.operations.map((operation) => operation.key),
      content: [],
      roots: ability.allowed_roots,
      axes: record.axes.filter((axis) =>
        ability.operations.some((operation) => axis.member === `operations/${operation.key}`),
      ),
      revision: ability.revision,
      source: "deployment",
    })),
    bound_tools: tree.bound_tools,
    surface: {
      mode: "auto",
      static: [],
      effective: tree.bound_tools.map((tool) => ({
        tool_name: tool.name,
        member: tool.member,
        package: "ghrah.fs",
        static: false,
        available:
          record.axes.find((axis) => axis.member === `operations/${tool.member}`)?.available !==
          false,
      })),
      current_runtime: record.runtimeTools,
      pending_respawn: {
        added:
          record.runtimeTools === null
            ? []
            : tree.bound_tools
                .filter((tool) => !record.runtimeTools!.includes(tool.name))
                .map((tool) => tool.name),
        removed:
          record.runtimeTools?.filter(
            (name) => !tree.bound_tools.some((tool) => tool.name === name),
          ) ?? [],
      },
    },
    changes_cursor: record.changes.length,
  });
}

function statusView(projectId: string, packagesRead: boolean) {
  void projectId;
  return AbilityApiStatusSchema.parse({
    format: "ghrah.ability-control.v1",
    api_version: 1,
    ability_source: "builtin",
    legacy_deprecated: false,
    operator: "deployment",
    features: {
      tree: true,
      grants_write: true,
      versions_read: false,
      versions_write: false,
      packages_read: packagesRead,
      packages_install: false,
      surface_write: false,
    },
    reason: null,
  });
}

/** 包组 fixture 里作为「当前选中」的摘要；与 defaultTree 的版本摘要一致。 */
const SELECTED_DIGEST = "sha256:version";

/**
 * 确定性包组名单，覆盖观察端要呈现的状态：已安装遮蔽内置、已提交并选
 * 中、授予跟随 ability-tree、以及关系库里有但发行版里找不到的版本。
 * distribution 只含定义面组；project/agent 范围由调用方补关系事实。
 */
function packageGroupsView(
  scope: "distribution" | "project" | "agent",
  projectId: string | null,
  agentId: string | null,
  grantedAliases: Set<string>,
): AbilityPackageGroupsView {
  const granted = (alias: string) => alias !== "" && grantedAliases.has(alias);
  const project = (digest: string | null, instanceId: string | null, selected: boolean) =>
    scope === "distribution"
      ? null
      : {
          committed: true,
          digest,
          instance_id: instanceId,
          selected,
          instance_revision: selected ? 1 : null,
        };
  const agent = (alias: string, instanceId: string | null) =>
    scope === "agent" && granted(alias) && instanceId !== null
      ? { granted: true, alias }
      : scope === "agent"
        ? { granted: false, alias: null }
        : null;
  return AbilityPackageGroupsViewSchema.parse({
    format: "ghrah.ability-control.package-groups.v1",
    scope,
    installed_available: true,
    project_id: projectId,
    agent_id: agentId,
    groups: [
      {
        namespace: "demo",
        name: "notes",
        full_name: "demo.notes",
        title: "Notes",
        versions: [
          {
            version: "0.3.0",
            origin: "installed",
            lock_state: "installed",
            problem: null,
            shadowed_builtin: true,
            member_count: 3,
            project: project("sha256:notes-installed", "notes-installed", false),
            agent: agent("notes", scope === "distribution" ? null : "notes-installed"),
          },
          {
            version: "0.3.0",
            origin: "builtin",
            lock_state: "builtin",
            problem: null,
            shadowed_builtin: false,
            member_count: 3,
            project: null,
            agent: null,
          },
        ],
      },
      {
        namespace: "ghrah",
        name: "fs",
        full_name: "ghrah.fs",
        title: "Filesystem",
        versions: [
          {
            version: "1",
            origin: "builtin",
            lock_state: "builtin",
            problem: null,
            shadowed_builtin: false,
            member_count: 1,
            project: project(SELECTED_DIGEST, "ghrah-fs", true),
            agent: agent("ghrah-fs", "ghrah-fs"),
          },
        ],
      },
      ...(scope === "distribution"
        ? []
        : [
            {
              namespace: null,
              name: null,
              full_name: null,
              title: null,
              versions: [
                {
                  version: null,
                  origin: null,
                  lock_state: null,
                  problem: "definition_missing",
                  shadowed_builtin: false,
                  member_count: null,
                  project: project("sha256:missing", null, false),
                  agent: agent("", null),
                },
              ],
            },
          ]),
    ],
  });
}

function writeCors(response: ServerResponse): void {
  response.setHeader("Access-Control-Allow-Origin", "*");
  response.setHeader("Access-Control-Allow-Methods", "GET, POST, PATCH, PUT, DELETE, OPTIONS");
  response.setHeader("Access-Control-Allow-Headers", "Authorization, Content-Type, Accept");
}

function json(response: ServerResponse, status: number, body: unknown): void {
  const payload = JSON.stringify(body);
  writeCors(response);
  response.writeHead(status, {
    "Content-Type": "application/json; charset=utf-8",
    "Content-Length": Buffer.byteLength(payload),
  });
  response.end(payload);
}

function conflict(response: ServerResponse, tree: AbilityTreeView): void {
  json(response, 409, {
    error: {
      code: "conflict",
      message: "relation revision changed",
      detail: { revision: tree.revision, view: tree },
    },
  });
}

async function readBody(request: IncomingMessage): Promise<unknown> {
  const chunks: Buffer[] = [];
  for await (const chunk of request) chunks.push(chunk as Buffer);
  if (chunks.length === 0) return undefined;
  try {
    return JSON.parse(Buffer.concat(chunks).toString("utf-8"));
  } catch {
    return undefined;
  }
}

const GRANT_PATH = /^\/ability-tree\/grants\/([^/]+)$/;
const TOOL_PATH = /^\/ability-tree\/tools\/([^/]+)$/;
const OPERATION_KEY = /^[a-z][a-z0-9_-]{0,63}$/;

function isRecord(value: unknown): value is Record<string, unknown> {
  return typeof value === "object" && value !== null;
}

function expectedRevision(method: string, url: URL, body: unknown): number | undefined {
  const raw =
    method === "DELETE"
      ? url.searchParams.get("expected_revision")
      : isRecord(body)
        ? body.expected_revision
        : undefined;
  const value = typeof raw === "number" ? raw : typeof raw === "string" ? Number(raw) : Number.NaN;
  return Number.isInteger(value) && value >= 0 ? value : undefined;
}

function bump(record: AgentRecord): void {
  record.tree = { ...record.tree, revision: record.tree.revision + 1 };
}

function applyGrant(record: AgentRecord, alias: string, body: unknown): string | null {
  const ability = defaultTree(record.tree.project_id, record.tree.agent_id).abilities.find(
    (item) => item.alias === alias,
  );
  if (!ability || !isRecord(body)) return "invalid_change";
  const requested = Array.isArray(body.members)
    ? body.members.filter((item) => typeof item === "string")
    : [];
  if (requested.some((item) => !OPERATION_KEY.test(item))) return "invalid_change";
  const known = new Set(ability.operations.map((operation) => operation.key));
  if (requested.length === 0 || requested.some((item) => !known.has(item))) return "invalid_change";
  const axes = Array.isArray(body.axes) ? body.axes.filter(isRecord) : [];
  const nextAxes: MemberAxes[] = [];
  for (const axis of axes) {
    if (typeof axis.member !== "string" || typeof axis.visible !== "boolean")
      return "invalid_change";
    if (!requested.some((item) => axis.member === `operations/${item}`)) return "invalid_change";
    nextAxes.push({
      member: axis.member,
      visible: axis.visible,
      available: axis.visible && axis.available !== false,
    });
  }
  record.tree = {
    ...record.tree,
    abilities: [
      ...record.tree.abilities.filter((item) => item.alias !== alias),
      {
        ...ability,
        operations: ability.operations.filter((operation) => requested.includes(operation.key)),
      },
    ],
    bound_tools: record.tree.bound_tools.filter(
      (tool) => tool.alias !== alias || requested.includes(tool.member),
    ),
  };
  record.axes = [
    ...record.axes.filter((axis) => !known.has(axis.member.replace(/^operations\//, ""))),
    ...nextAxes,
  ];
  record.requiresRespawn =
    requested.length < known.size || nextAxes.some((axis) => !axis.visible || !axis.available);
  bump(record);
  return null;
}

function applyRevoke(record: AgentRecord, alias: string): void {
  const removed = new Set(
    record.tree.abilities
      .find((item) => item.alias === alias)
      ?.operations.map((operation) => operation.key) ?? [],
  );
  record.tree = {
    ...record.tree,
    abilities: record.tree.abilities.filter((item) => item.alias !== alias),
    bound_tools: record.tree.bound_tools.filter((tool) => tool.alias !== alias),
  };
  record.axes = record.axes.filter(
    (axis) => !removed.has(axis.member.replace(/^operations\//, "")),
  );
  record.requiresRespawn = true;
  bump(record);
}

function applyBind(record: AgentRecord, toolName: string, body: unknown): string | null {
  if (!isRecord(body) || body.tool_name !== toolName) return "invalid_change";
  if (typeof body.alias !== "string" || typeof body.member !== "string") return "invalid_change";
  const ability = record.tree.abilities.find((item) => item.alias === body.alias);
  if (!ability?.operations.some((operation) => operation.key === body.member))
    return "invalid_change";
  const alias = body.alias;
  const member = body.member;
  record.tree = {
    ...record.tree,
    bound_tools: [
      ...record.tree.bound_tools.filter((tool) => tool.name !== toolName),
      {
        name: toolName,
        alias,
        member,
        grant_revision: record.tree.revision,
        revision: record.tree.revision,
      },
    ],
  };
  record.requiresRespawn = true;
  bump(record);
  return null;
}

function applyUnbind(record: AgentRecord, toolName: string): void {
  record.tree = {
    ...record.tree,
    bound_tools: record.tree.bound_tools.filter((tool) => tool.name !== toolName),
  };
  record.requiresRespawn = true;
  bump(record);
}

const PROJECT_AGENT = /^\/api\/v1\/projects\/([^/]+)\/agents\/([^/]+)(\/.*)?$/;
const AGENT_RESOURCE = /^\/api\/v1\/projects\/([^/]+)\/agents(?:\/([^/]+))?(\/desired-state)?$/;
const CONTENT =
  /^\/api\/v1\/projects\/([^/]+)\/agents\/([^/]+)\/ability-tree\/abilities\/([^/]+)\/content\/(.+)$/;

/** 观察端 REST 管理面的确定性 mock；只服务演示与契约测试，不实现授权规则。 */
export function createMockHttpServer(options: MockHttpServerOptions): MockHttpServer {
  const projectId = options.projectId ?? DEFAULT_PROJECT;
  const packagesEnabled = options.packages ?? true;
  const agents = new Map<string, AgentRecord>();
  let server: Server | undefined;

  function recordFor(pid: string, aid: string): AgentRecord {
    const key = agentKey(pid, aid);
    const existing = agents.get(key);
    if (existing) return existing;
    const tree = defaultTree(pid, aid);
    const created: AgentRecord = {
      tree,
      axes: defaultAxes(tree),
      requiresRespawn: false,
      changes: [],
      runtimeIncarnation: null,
      runtimeTools: null,
    };
    agents.set(key, created);
    return created;
  }

  function agentView(pid: string, aid: string, effective: "next_start" | "stored" | null = null) {
    const result = options.state!.handleCommand(CommandType.AGENT_ENTITY, {
      project_id: pid,
      agent_id: aid,
      operation: { type: "snapshot" },
    });
    const snapshot = AgentSnapshotSchema.parse(result.data);
    const spec = snapshot.specification;
    const record = recordFor(pid, aid);
    syncRuntime(record, snapshot.runtime.incarnation_id);
    return AgentAdminViewSchema.parse({
      project_id: pid,
      specification: {
        agent_id: aid,
        name: spec.name,
        endpoint_name: spec.endpoint_name ?? null,
        system_prompt: spec.system_prompt,
        prompt: {
          digest: `sha256:${createHash("sha256").update(spec.system_prompt).digest("hex")}`,
          size_bytes: Buffer.byteLength(spec.system_prompt),
        },
        desired_state: spec.desired_state,
        manifest_ref: spec.manifest_ref,
        runtime_status: spec.runtime_status,
        runtime_error: spec.runtime_error,
        project_version: snapshot.specification_revision,
      },
      runtime: snapshot.runtime,
      ability: {
        revision: record.tree.revision,
        state: snapshot.runtime.availability === "online" ? "running" : "stopped",
        requires_respawn: record.requiresRespawn,
      },
      effective,
      spec_overrides_template: Boolean(spec.manifest_ref) && effective === "next_start",
    });
  }

  async function handle(request: IncomingMessage, response: ServerResponse): Promise<void> {
    const method = request.method ?? "GET";
    const url = new URL(request.url ?? "/", "http://mock");
    const path = url.pathname;

    if (method === "OPTIONS") {
      writeCors(response);
      response.writeHead(204);
      response.end();
      return;
    }

    if (method === "GET" && path === "/health") {
      json(response, 200, { status: "ok" });
      return;
    }
    if (method === "GET" && path === "/api/v1/agentconf/endpoints") {
      json(
        response,
        200,
        AgentconfEndpointListViewSchema.parse({
          endpoints: [
            {
              id: "mock-endpoint",
              name: "mock-model",
              provider_id: "mock-provider",
              provider_name: "mock",
              model_name: "mock-model",
              model_type: "llm",
              api_format: "openai",
              revision: 1,
              created_at: "2026-01-01T00:00:00Z",
              updated_at: "2026-01-01T00:00:00Z",
            },
          ].filter(
            (item) =>
              !url.searchParams.has("model_type") ||
              item.model_type === url.searchParams.get("model_type"),
          ),
        }),
      );
      return;
    }
    const resource = AGENT_RESOURCE.exec(path);
    if (resource) {
      const state = options.state;
      if (!state) {
        json(response, 503, {
          error: { code: "capability_unavailable", message: "shared MockState required" },
        });
        return;
      }
      const [, pid, aid, action] = resource;
      const project = state.projects.get(pid);
      const spec = project?.agents.find((item) => item.agent_id === aid);
      if (!project || project.archived_at || (aid && !spec)) {
        json(response, 404, { error: { code: "not_found", message: "unknown project or Agent" } });
        return;
      }
      if (method === "GET" && !action) {
        if (aid) json(response, 200, agentView(pid, aid));
        else
          json(
            response,
            200,
            AgentListViewSchema.parse({
              project_id: pid,
              agents: project.agents.map((agent) => {
                const view = agentView(pid, agent.agent_id!);
                const { system_prompt: systemPrompt, ...summary } = view.specification;
                void systemPrompt;
                return {
                  project_id: pid,
                  specification: summary,
                  runtime: view.runtime,
                  ability: view.ability,
                  ability_unavailable: view.ability_unavailable,
                };
              }),
            }),
          );
        return;
      }
      const body = method === "DELETE" ? undefined : await readBody(request);
      let outcome: CommandOutcome | undefined;
      let targetId = aid;
      if (method === "POST" && !aid) {
        const parsed = AgentCreateRequestSchema.safeParse(body);
        if (
          parsed.success &&
          (!parsed.data.endpoint_name || parsed.data.endpoint_name === "mock-model")
        ) {
          const change = parsed.data;
          outcome = state.handleCommand(CommandType.PROJECT_ADD_AGENT, {
            project_id: pid,
            expected_version: change.expected_revision,
            agent: {
              name: change.name,
              endpoint_name: change.endpoint_name,
              system_prompt: change.system_prompt,
              desired_state: change.desired_state,
            },
          });
          if (outcome.success) targetId = (outcome.data as { agent_id: string }).agent_id;
        } else if (parsed.success) {
          outcome = { success: false, error: "model_configuration_invalid" };
        }
      } else if (method === "PATCH" && aid && !action) {
        outcome = isRecord(body)
          ? state.patchAgentSpecification(pid, aid, body)
          : { success: false, error: "invalid_request" };
      } else if (method === "PUT" && aid && action) {
        const parsed = AgentDesiredStateRequestSchema.safeParse(body);
        if (parsed.success)
          outcome = state.handleCommand(CommandType.AGENT_ENTITY, {
            project_id: pid,
            agent_id: aid,
            operation: {
              type: parsed.data.state === "running" ? "start" : "stop",
              expected_revision: parsed.data.expected_revision,
            },
          });
      } else if (method === "DELETE" && aid && !action) {
        const revision = expectedRevision(method, url, body);
        if (revision !== undefined)
          outcome = state.handleCommand(CommandType.PROJECT_REMOVE_AGENT, {
            project_id: pid,
            agent_id: aid,
            agent_name: spec!.name,
            expected_version: revision,
          });
      } else {
        json(response, 405, { error: { code: "invalid_request", message: "method not allowed" } });
        return;
      }
      if (!outcome?.success) {
        const code =
          outcome?.error === "agent_specification_conflict"
            ? "conflict"
            : outcome?.error?.startsWith("invalid payload:")
              ? "invalid_request"
              : (outcome?.error ?? "invalid_request");
        json(
          response,
          code === "conflict" ||
            code === "agent_name_exists" ||
            code === "agent_manifest_required" ||
            code === "project_not_active"
            ? 409
            : 400,
          {
            error: {
              code,
              message: outcome?.errorDetail ?? code,
              detail: { revision: project.version },
            },
          },
        );
        return;
      }
      if (method === "DELETE") {
        agents.delete(agentKey(pid, aid));
        json(
          response,
          200,
          AgentDeletedViewSchema.parse({
            project_id: pid,
            agent_id: aid,
            project_version: project.version,
          }),
        );
      } else {
        if (method === "POST") response.setHeader("Location", `${path}/${targetId}`);
        json(
          response,
          method === "POST" ? 201 : 200,
          agentView(
            pid,
            targetId!,
            method === "PATCH" ? "next_start" : method === "PUT" ? "stored" : null,
          ),
        );
      }
      return;
    }
    const scopedAgent = PROJECT_AGENT.exec(path);
    if (options.state && scopedAgent) {
      const [, pid, aid] = scopedAgent;
      const project = options.state.projects.get(pid);
      if (
        !project ||
        project.archived_at ||
        !project.agents.some((agent) => agent.agent_id === aid)
      ) {
        json(response, 404, { error: { code: "agent_not_found", message: "unknown mock agent" } });
        return;
      }
    }

    if (method === "GET" && path === "/api/v1/abilities/status") {
      json(response, 200, statusView(projectId, packagesEnabled));
      return;
    }

    const DISTRIBUTION_GROUPS = "/api/v1/ability-package-groups";
    const PROJECT_GROUPS = /^\/api\/v1\/projects\/([^/]+)\/ability-package-groups$/;
    const AGENT_GROUPS = /^\/api\/v1\/projects\/([^/]+)\/agents\/([^/]+)\/ability-package-groups$/;
    const agentGroupsMatch = AGENT_GROUPS.exec(path);
    const projectGroupsMatch = PROJECT_GROUPS.exec(path);
    if (method === "GET" && (path === DISTRIBUTION_GROUPS || projectGroupsMatch)) {
      if (!packagesEnabled) {
        json(response, 503, {
          error: { code: "capability_unavailable", message: "packages disabled" },
        });
        return;
      }
      const pid = projectGroupsMatch?.[1] ?? null;
      // project 范围不带授予事实；scope 参数本身决定行上哪些字段为空。
      json(
        response,
        200,
        packageGroupsView(pid === null ? "distribution" : "project", pid, null, new Set()),
      );
      return;
    }
    if (method === "GET" && agentGroupsMatch) {
      if (!packagesEnabled) {
        json(response, 503, {
          error: { code: "capability_unavailable", message: "packages disabled" },
        });
        return;
      }
      const [, pid, aid] = agentGroupsMatch;
      const grantedAliases = new Set(recordFor(pid, aid).tree.abilities.map((a) => a.alias));
      json(response, 200, packageGroupsView("agent", pid, aid, grantedAliases));
      return;
    }

    const contentMatch = CONTENT.exec(path);
    if (method === "GET" && contentMatch) {
      const [, pid, aid, alias, member] = contentMatch;
      const body = `mock content for ${member}`;
      json(
        response,
        200,
        ContentReadResultSchema.parse({
          instance_id: alias,
          version_digest: "sha256:version",
          member,
          path: member,
          media_type: "text/markdown",
          digest: "sha256:content",
          total_bytes: Buffer.byteLength(body),
          offset: Number(url.searchParams.get("offset") ?? 0),
          byte_count: Buffer.byteLength(body),
          next_offset: null,
          encoding: "utf-8",
          content: body,
        }),
      );
      void pid;
      void aid;
      return;
    }

    const match = PROJECT_AGENT.exec(path);
    if (!match) {
      json(response, 404, { error: { code: "not_found", message: `no mock route: ${path}` } });
      return;
    }
    const [, pid, aid, rest = ""] = match;
    const record = recordFor(pid, aid);
    const runtime = options.state
      ? [...options.state.agents.values()].find(
          (agent) => agent.projectId === pid && agent.agentId === aid,
        )
      : undefined;
    if (options.state)
      syncRuntime(record, runtime?.runtimeState === "running" ? runtime.incarnationId : null);

    if (method === "GET" && rest === "/ability-tree") {
      json(response, 200, record.tree);
      return;
    }
    if (method === "GET" && rest === "/ability-admin") {
      json(response, 200, adminView(record, runtime?.runtimeState === "running"));
      return;
    }
    if (method === "GET" && rest === "/ability-changes") {
      json(
        response,
        200,
        AbilityChangesPageSchema.parse({
          format: "ghrah.ability-control.changes.v1",
          changes: record.changes.filter(
            (change) => change.seq > Number(url.searchParams.get("after_seq") ?? 0),
          ),
          next_after_seq: record.changes.length,
          has_more: false,
        }),
      );
      return;
    }

    const grantMatch = GRANT_PATH.exec(rest);
    const toolMatch = TOOL_PATH.exec(rest);
    if ((method === "PUT" || method === "DELETE") && (grantMatch || toolMatch)) {
      const body = method === "PUT" ? await readBody(request) : undefined;
      if (options.conflictOnWrite || url.searchParams.get("conflict") === "1") {
        conflict(response, record.tree);
        return;
      }
      const revision = expectedRevision(method, url, body);
      if (revision === undefined) {
        json(response, 400, {
          error: { code: "invalid_request", message: "expected_revision required" },
        });
        return;
      }
      if (revision !== record.tree.revision) {
        conflict(response, record.tree);
        return;
      }
      const alias = grantMatch?.[1];
      const toolName = toolMatch?.[1];
      let error: string | null = "invalid_change";
      if (method === "PUT" && alias) error = applyGrant(record, alias, body);
      else if (method === "DELETE" && alias) {
        applyRevoke(record, alias);
        error = null;
      } else if (method === "PUT" && toolName) error = applyBind(record, toolName, body);
      else if (method === "DELETE" && toolName) {
        applyUnbind(record, toolName);
        error = null;
      }
      if (error) {
        json(response, 400, { error: { code: error, message: "mock rejected the change" } });
        return;
      }
      record.changes.push({
        seq: record.changes.length + 1,
        agent: aid,
        revision: record.tree.revision,
        before_revision: revision,
        reason: grantMatch
          ? method === "PUT"
            ? "grant"
            : "revoke"
          : method === "PUT"
            ? "bind_tool"
            : "unbind_tool",
        actor: { operator: "mock" },
        source: "deployment",
        version_digest: null,
        policy: null,
      });
      options.state?.emit(EventType.ABILITY_TREE_CHANGED, {
        project_id: pid,
        agent_id: aid,
        revision: record.tree.revision,
        reason: record.changes.at(-1)!.reason,
      });
      json(
        response,
        200,
        AbilityTreeChangeResultSchema.parse({
          format: "ghrah.ability-control.change.v1",
          view: record.tree,
          revision: record.tree.revision,
        }),
      );
      return;
    }
    json(response, 405, { error: { code: "invalid_request", message: "method not allowed" } });
  }

  return {
    get port() {
      const address = server?.address();
      return address && typeof address === "object" ? address.port : options.port;
    },
    async start() {
      server = createServer((request, response) => {
        handle(request, response).catch((error: unknown) => {
          options.logger?.(`http mock error: ${String(error)}`);
          json(response, 500, { error: { code: "internal", message: "mock failure" } });
        });
      });
      await new Promise<void>((resolve, reject) => {
        server?.once("error", reject);
        server?.listen(options.port, options.host, () => resolve());
      });
      options.logger?.(`http mock listening on ${this.port}`);
    },
    async close() {
      if (!server?.listening) return;
      await new Promise<void>((resolve, reject) => {
        server?.close((error) => (error ? reject(error) : resolve()));
      });
      server = undefined;
    },
  };
}
