// SPDX-FileCopyrightText: 2026 chenxya <chenxya@ghrah.org>
//
// SPDX-License-Identifier: Apache-2.0

import type {
  AbilityAdminView,
  AbilityChangeView,
  AbilityGrantView,
  AbilityTreeView,
  BindToolRequest,
  GrantAbilityRequest,
  MemberAxes,
  SkillReadScope,
} from "@ghrah/protocol";
import { defineStore } from "pinia";
import { ref, triggerRef } from "vue";

import { abilityControlClient } from "../ability-client.js";
import { AbilityConflictError } from "../ability-control.js";

export function abilityAdminKey(projectId: string, agentId: string): string {
  return `${projectId}:${agentId}`;
}

export interface AbilityAdminRecord {
  projectId: string;
  agentId: string;
  tree: AbilityTreeView | null;
  admin: AbilityAdminView | null;
  changes: AbilityChangeView[];
  nextAfterSeq: number;
  hasMore: boolean;
  revision: number | null;
  loading: boolean;
  stale: boolean;
  failed: boolean;
  conflict: boolean;
  errorMessage: string | null;
  loadedAt: number | null;
}

export interface AbilityFocus {
  projectId: string;
  agentId: string;
}

const PAGE_LIMIT = 50;

function blank(projectId: string, agentId: string): AbilityAdminRecord {
  return {
    projectId,
    agentId,
    tree: null,
    admin: null,
    changes: [],
    nextAfterSeq: 0,
    hasMore: false,
    revision: null,
    loading: false,
    stale: false,
    failed: false,
    conflict: false,
    errorMessage: null,
    loadedAt: null,
  };
}

function errorText(error: unknown): string {
  return error instanceof Error && error.message ? error.message : "request failed";
}

const OPERATION_KEY = /^[a-z][a-z0-9_-]{0,63}$/;
const OPERATION_ADDRESS = /^operations\/([a-z][a-z0-9_-]{0,63})$/;
const CONTENT_ADDRESS =
  /^skill\/([a-z][a-z0-9_-]{0,63})(?:\/(assets|references|scripts)\/([a-z][a-z0-9_-]{0,63}))?$/;

function operationKey(member: string): string | null {
  if (OPERATION_KEY.test(member)) return member;
  return OPERATION_ADDRESS.exec(member)?.[1] ?? null;
}

/** 把授权视图里的成员收成可提交的 Operation 键。带前缀的地址只取键。 */
export function grantOperationKeys(members: readonly string[]): string[] {
  const keys: string[] = [];
  for (const member of members) {
    const key = operationKey(member);
    if (key && !keys.includes(key)) keys.push(key);
  }
  return keys;
}

function skillsFromContent(content: readonly string[]): SkillReadScope[] {
  const scopes = new Map<
    string,
    { body: boolean; assets: string[]; references: string[]; scripts: string[] }
  >();
  for (const item of content) {
    const match = CONTENT_ADDRESS.exec(item);
    if (!match?.[1]) continue;
    const scope = scopes.get(match[1]) ?? { body: false, assets: [], references: [], scripts: [] };
    const role = match[2];
    if (!role) scope.body = true;
    else if (match[3] && (role === "assets" || role === "references" || role === "scripts")) {
      scope[role].push(match[3]);
    }
    scopes.set(match[1], scope);
  }
  return [...scopes.entries()].map(([key, scope]) => ({
    key,
    body: scope.body,
    assets: scope.assets,
    references: scope.references,
    scripts: scope.scripts,
  }));
}

/** 用当前授权的实例、根路径和内容范围组装整条替换请求，不新增成员。 */
export function replacementGrant(
  grant: AbilityGrantView,
  expectedRevision: number,
  selection: { members: readonly string[]; axes: readonly MemberAxes[] },
): GrantAbilityRequest {
  const members = grantOperationKeys(selection.members);
  const allowed = new Set(members.map((key) => `operations/${key}`));
  return {
    alias: grant.alias,
    instance_id: grant.instance_id,
    members,
    allowed_roots: [...grant.roots],
    skill: null,
    skills: skillsFromContent(grant.content),
    whole_container: false,
    axes: selection.axes.filter(
      (axis) => allowed.has(axis.member) || axis.member.startsWith("skill/"),
    ),
    policy: null,
    expected_revision: expectedRevision,
  };
}

export const useAbilityAdminStore = defineStore("ghrah-ability-admin", () => {
  const records = ref(new Map<string, AbilityAdminRecord>());
  const visible = ref(new Set<string>());
  const focus = ref<AbilityFocus | null>(null);
  const inflight = new Map<string, Promise<void>>();
  const queued = new Set<string>();
  const generation = new Map<string, number>();

  function record(projectId: string, agentId: string): AbilityAdminRecord | null {
    return records.value.get(abilityAdminKey(projectId, agentId)) ?? null;
  }

  function publish() {
    triggerRef(records);
  }

  function ensure(projectId: string, agentId: string): AbilityAdminRecord {
    const key = abilityAdminKey(projectId, agentId);
    const existing = records.value.get(key);
    if (existing) return existing;
    const created = blank(projectId, agentId);
    records.value.set(key, created);
    return created;
  }

  function focusAgent(projectId: string, agentId: string) {
    focus.value = { projectId, agentId };
  }

  function setVisible(
    projectId: string,
    agentId: string,
    shown: boolean,
  ): Promise<void> | undefined {
    const key = abilityAdminKey(projectId, agentId);
    const next = new Set(visible.value);
    if (shown) next.add(key);
    else next.delete(key);
    visible.value = next;
    if (!shown) return undefined;
    const entry = records.value.get(key);
    if (!entry || entry.stale || entry.loadedAt === null) return load(projectId, agentId);
    return undefined;
  }

  async function load(projectId: string, agentId: string): Promise<void> {
    const key = abilityAdminKey(projectId, agentId);
    const pending = inflight.get(key);
    if (pending) {
      queued.add(key);
      await pending;
      if (!queued.delete(key)) return;
    }
    const current = (generation.get(key) ?? 0) + 1;
    generation.set(key, current);
    const task = read(projectId, agentId, key, current);
    inflight.set(key, task);
    try {
      await task;
    } finally {
      if (inflight.get(key) === task) inflight.delete(key);
    }
  }

  async function read(
    projectId: string,
    agentId: string,
    key: string,
    current: number,
  ): Promise<void> {
    const entry = ensure(projectId, agentId);
    entry.loading = true;
    entry.failed = false;
    entry.errorMessage = null;
    publish();
    try {
      const client = abilityControlClient(projectId);
      const [treeResult, adminResult, changesResult] = await Promise.allSettled([
        client.tree(agentId),
        client.admin(agentId),
        client.changes(agentId, { limit: PAGE_LIMIT }),
      ]);
      if (generation.get(key) !== current) return;
      const adminOk = adminResult.status === "fulfilled";
      const treeOk = treeResult.status === "fulfilled";
      if (!adminOk && !treeOk) {
        entry.failed = true;
        entry.errorMessage = errorText(
          adminResult.status === "rejected" ? adminResult.reason : treeResult.reason,
        );
        return;
      }
      if (adminOk) {
        entry.admin = adminResult.value;
        entry.revision = adminResult.value.revision;
      }
      if (treeOk) entry.tree = treeResult.value;
      if (changesResult.status === "fulfilled") {
        entry.changes = changesResult.value.changes.filter((change) => change.agent === agentId);
        entry.nextAfterSeq = changesResult.value.next_after_seq;
        entry.hasMore = changesResult.value.has_more;
      }
      entry.stale = false;
      entry.conflict = false;
      entry.loadedAt = Date.now();
    } catch (error) {
      if (generation.get(key) !== current) return;
      entry.failed = true;
      entry.errorMessage = errorText(error);
    } finally {
      if (generation.get(key) === current) entry.loading = false;
      publish();
    }
  }

  async function loadMore(projectId: string, agentId: string): Promise<void> {
    const entry = record(projectId, agentId);
    if (!entry || !entry.hasMore || entry.loading) return;
    entry.loading = true;
    publish();
    try {
      const page = await abilityControlClient(projectId).changes(agentId, {
        afterSeq: entry.nextAfterSeq,
        limit: PAGE_LIMIT,
      });
      const seen = new Set(entry.changes.map((change) => change.seq));
      for (const change of page.changes) {
        if (change.agent === agentId && !seen.has(change.seq)) entry.changes.push(change);
      }
      entry.nextAfterSeq = page.next_after_seq;
      entry.hasMore = page.has_more;
    } catch (error) {
      entry.failed = true;
      entry.errorMessage = errorText(error);
    } finally {
      entry.loading = false;
      publish();
    }
  }

  async function settleWrite(
    projectId: string,
    agentId: string,
    action: () => Promise<{ view: AbilityTreeView | null; revision: number }>,
  ): Promise<void> {
    const entry = record(projectId, agentId);
    if (!entry || entry.revision === null || entry.loading) return;
    entry.loading = true;
    entry.failed = false;
    entry.errorMessage = null;
    publish();
    try {
      const result = await action();
      entry.conflict = false;
      entry.revision = result.revision;
      if (result.view) entry.tree = result.view;
      publish();
    } catch (error) {
      if (error instanceof AbilityConflictError) {
        entry.conflict = true;
        entry.failed = false;
        if (error.revision !== undefined) entry.revision = error.revision;
        if (error.view) entry.tree = error.view;
        entry.errorMessage = error.message;
        entry.loading = false;
        publish();
        return;
      }
      entry.failed = true;
      entry.errorMessage = errorText(error);
      entry.loading = false;
      publish();
      return;
    }
    await load(projectId, agentId);
  }

  function replaceGrant(
    projectId: string,
    agentId: string,
    request: GrantAbilityRequest,
  ): Promise<void> {
    return settleWrite(projectId, agentId, () =>
      abilityControlClient(projectId).putGrant(agentId, request),
    );
  }

  function revokeGrant(projectId: string, agentId: string, alias: string): Promise<void> {
    const revision = record(projectId, agentId)?.revision;
    if (revision === null || revision === undefined) return Promise.resolve();
    return settleWrite(projectId, agentId, () =>
      abilityControlClient(projectId).revokeGrant(agentId, {
        alias,
        expected_revision: revision,
        members: null,
      }),
    );
  }

  function bindTool(projectId: string, agentId: string, request: BindToolRequest): Promise<void> {
    return settleWrite(projectId, agentId, () =>
      abilityControlClient(projectId).bindTool(agentId, request),
    );
  }

  function unbindTool(projectId: string, agentId: string, toolName: string): Promise<void> {
    const revision = record(projectId, agentId)?.revision;
    if (revision === null || revision === undefined) return Promise.resolve();
    return settleWrite(projectId, agentId, () =>
      abilityControlClient(projectId).unbindTool(agentId, {
        tool_name: toolName,
        expected_revision: revision,
      }),
    );
  }

  async function markStale(projectId: string, agentId: string): Promise<void> {
    if (!projectId || !agentId) return;
    const entry = ensure(projectId, agentId);
    entry.stale = true;
    publish();
    if (visible.value.has(abilityAdminKey(projectId, agentId))) await load(projectId, agentId);
  }

  async function reloadVisible(): Promise<void> {
    for (const entry of records.value.values()) entry.stale = true;
    publish();
    await Promise.all(
      [...visible.value].map((key) => {
        const entry = records.value.get(key);
        return entry ? load(entry.projectId, entry.agentId) : Promise.resolve();
      }),
    );
  }

  function clearProject(projectId: string) {
    for (const [key, entry] of records.value) {
      if (entry.projectId !== projectId) continue;
      records.value.delete(key);
      visible.value = new Set([...visible.value].filter((item) => item !== key));
    }
    if (focus.value?.projectId === projectId) focus.value = null;
    publish();
  }

  return {
    records,
    visible,
    focus,
    record,
    focusAgent,
    setVisible,
    load,
    loadMore,
    replaceGrant,
    revokeGrant,
    bindTool,
    unbindTool,
    markStale,
    reloadVisible,
    clearProject,
  };
});
