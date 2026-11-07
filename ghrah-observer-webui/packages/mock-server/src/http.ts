import { createServer, type IncomingMessage, type Server, type ServerResponse } from "node:http";

import {
  AbilityAdminViewSchema,
  AbilityApiStatusSchema,
  AbilityChangesPageSchema,
  AbilityTreeChangeResultSchema,
  AbilityTreeViewSchema,
  ContentReadResultSchema,
  type AbilityTreeView,
} from "@ghrah/protocol";

export interface MockHttpServerOptions {
  port: number;
  host?: string;
  projectId?: string;
  agentId?: string;
  /** 写端点固定返回 409 冲突，供前端冲突分支联调。 */
  conflictOnWrite?: boolean;
  logger?: (line: string) => void;
}

export interface MockHttpServer {
  readonly port: number;
  start(): Promise<void>;
  close(): Promise<void>;
}

const DEFAULT_PROJECT = "project-1";
const DEFAULT_AGENT = "agent-1";

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

function adminView(tree: AbilityTreeView) {
  return AbilityAdminViewSchema.parse({
    format: "ghrah.ability-control.admin.v1",
    project_id: tree.project_id,
    agent_id: tree.agent_id,
    revision: tree.revision,
    run_id: tree.run_id,
    lifecycle: { state: "stopped", requires_respawn: false, pending_reasons: [] },
    grant_policy: null,
    grants: tree.abilities.map((ability) => ({
      alias: ability.alias,
      instance_id: ability.instance_id,
      instance_revision: ability.instance_revision,
      version_digest: ability.version_digest,
      definition_digest: ability.definition_digest,
      members: ability.operations.map((operation) => `operations/${operation.key}`),
      content: [],
      roots: ability.allowed_roots,
      axes: [],
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
        available: true,
      })),
      current_runtime: tree.bound_tools.map((tool) => tool.name),
      pending_respawn: { added: [], removed: [] },
    },
    changes_cursor: 0,
  });
}

function statusView(projectId: string) {
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
      versions_read: true,
      versions_write: false,
      packages_read: false,
      packages_install: false,
      surface_write: false,
    },
    reason: null,
  });
}

function writeCors(response: ServerResponse): void {
  response.setHeader("Access-Control-Allow-Origin", "*");
  response.setHeader("Access-Control-Allow-Methods", "GET, PUT, DELETE, OPTIONS");
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
      detail: { revision: tree.revision + 1, view: { ...tree, revision: tree.revision + 1 } },
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

const PROJECT_AGENT = /^\/api\/v1\/projects\/([^/]+)\/agents\/([^/]+)(\/.*)?$/;
const CONTENT =
  /^\/api\/v1\/projects\/([^/]+)\/agents\/([^/]+)\/ability-tree\/abilities\/([^/]+)\/content\/(.+)$/;

/** 观察端 REST 管理面的确定性 mock；只服务演示与契约测试，不实现授权规则。 */
export function createMockHttpServer(options: MockHttpServerOptions): MockHttpServer {
  const projectId = options.projectId ?? DEFAULT_PROJECT;
  const agentId = options.agentId ?? DEFAULT_AGENT;
  let tree = defaultTree(projectId, agentId);
  let server: Server | undefined;

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

    if (method === "GET" && path === "/api/v1/abilities/status") {
      json(response, 200, statusView(projectId));
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

    const view = pid === projectId && aid === agentId ? tree : defaultTree(pid, aid);
    if ((pid !== projectId || aid !== agentId) && method !== "GET") {
      json(response, 404, { error: { code: "not_found", message: "unknown agent" } });
      return;
    }

    if (method === "GET" && rest === "/ability-tree") {
      json(response, 200, view);
      return;
    }
    if (method === "GET" && rest === "/ability-admin") {
      json(response, 200, adminView(view));
      return;
    }
    if (method === "GET" && rest === "/ability-changes") {
      json(
        response,
        200,
        AbilityChangesPageSchema.parse({
          format: "ghrah.ability-control.changes.v1",
          changes: [],
          next_after_seq: 0,
          has_more: false,
        }),
      );
      return;
    }
    if (method === "PUT" || method === "DELETE") {
      await readBody(request);
      if (options.conflictOnWrite || url.searchParams.get("conflict") === "1") {
        conflict(response, tree);
        return;
      }
      tree = { ...tree, revision: tree.revision + 1 };
      json(
        response,
        200,
        AbilityTreeChangeResultSchema.parse({
          format: "ghrah.ability-control.change.v1",
          view: tree,
          revision: tree.revision,
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
      await new Promise<void>((resolve) => {
        server?.listen(options.port, options.host, () => resolve());
      });
      options.logger?.(`http mock listening on ${this.port}`);
    },
    async close() {
      if (!server) return;
      await new Promise<void>((resolve, reject) => {
        server?.close((error) => (error ? reject(error) : resolve()));
      });
      server = undefined;
    },
  };
}
