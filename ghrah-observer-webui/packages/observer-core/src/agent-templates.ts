/** Agent 模板管理面（Subject REST `/api/v1/agent-templates`）。
 *
 * 模板是 Subject 侧资源：实例级、无项目前缀。这里只负责把 HTTP 形状折成协议视图，
 * 不做业务校验（身份唯一性、revision CAS 都由服务端裁决）。
 *
 * 版本语义：一行 = 一个版本，`revision` 递增；列表默认只返回每个模板的最新版本。
 */

import type { HttpClient } from "./http";
import {
  AgentTemplateDetailViewSchema,
  AgentTemplateListViewSchema,
  AgentTemplateReferencesViewSchema,
  AgentTemplateRevisionsViewSchema,
} from "@ghrah/protocol";
import type {
  AgentTemplateCreateRequest,
  AgentTemplateDetailView,
  AgentTemplateListView,
  AgentTemplateReferencesView,
  AgentTemplateRevisionsView,
  AgentTemplateUpdateRequest,
} from "@ghrah/protocol";

export interface AgentTemplateListQuery {
  namespace?: string | null;
  name?: string | null;
  tag?: string | null;
  deprecated?: boolean | null;
  includeArchived?: boolean;
  includeHistory?: boolean;
}

/** 模板读写门面；路径拼接与查询参数归一集中在这里，便于单测。 */
export class AgentTemplatesApi {
  constructor(private readonly http: HttpClient) {}

  private static base(): string {
    return "/api/v1/agent-templates";
  }

  private static item(templateId: string): string {
    return `${AgentTemplatesApi.base()}/${encodeURIComponent(templateId)}`;
  }

  async list(query: AgentTemplateListQuery = {}): Promise<AgentTemplateListView> {
    const raw = await this.http.get<unknown>(AgentTemplatesApi.base(), {
      query: {
        namespace: query.namespace,
        name: query.name,
        tag: query.tag,
        deprecated: query.deprecated,
        include_archived: query.includeArchived ? true : undefined,
        include_history: query.includeHistory ? true : undefined,
      },
    });
    return AgentTemplateListViewSchema.parse(raw);
  }

  async get(templateId: string): Promise<AgentTemplateDetailView> {
    const raw = await this.http.get<unknown>(AgentTemplatesApi.item(templateId));
    return AgentTemplateDetailViewSchema.parse(raw);
  }

  async create(body: AgentTemplateCreateRequest): Promise<AgentTemplateDetailView> {
    const raw = await this.http.post<unknown>(AgentTemplatesApi.base(), body);
    return AgentTemplateDetailViewSchema.parse(raw);
  }

  async update(
    templateId: string,
    body: AgentTemplateUpdateRequest,
  ): Promise<AgentTemplateDetailView> {
    const raw = await this.http.put<unknown>(AgentTemplatesApi.item(templateId), body);
    return AgentTemplateDetailViewSchema.parse(raw);
  }

  async versions(templateId: string): Promise<AgentTemplateRevisionsView> {
    const raw = await this.http.get<unknown>(`${AgentTemplatesApi.item(templateId)}/revisions`);
    return AgentTemplateRevisionsViewSchema.parse(raw);
  }

  async references(templateId: string): Promise<AgentTemplateReferencesView> {
    const raw = await this.http.get<unknown>(`${AgentTemplatesApi.item(templateId)}/references`);
    return AgentTemplateReferencesViewSchema.parse(raw);
  }

  async archive(templateId: string, expectedRevision: number): Promise<AgentTemplateDetailView> {
    const raw = await this.http.post<unknown>(`${AgentTemplatesApi.item(templateId)}/archive`, {
      expected_revision: expectedRevision,
    });
    return AgentTemplateDetailViewSchema.parse(raw);
  }
}

export function createAgentTemplatesApi(http: HttpClient): AgentTemplatesApi {
  return new AgentTemplatesApi(http);
}
