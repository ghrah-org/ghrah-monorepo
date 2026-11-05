// SPDX-FileCopyrightText: 2026 chenxya <chenxya@ghrah.org>
//
// SPDX-License-Identifier: Apache-2.0

import type {
  SessionActivatedPayload,
  SessionArchivedPayload,
  SessionCreatedPayload,
  SessionDeletedPayload,
  SessionInfoPayload,
} from "@ghrah/protocol";
import { defineStore } from "pinia";
import { ref } from "vue";
import { type AgentTarget, agentKey, type SessionTarget, sessionKey } from "../scope.js";

export interface SessionProjection {
  target: SessionTarget;
  info: SessionInfoPayload;
}

interface ActiveSessionSelection {
  target: AgentTarget;
  sessionId: string;
}

/** list 请求发出前捕获的实体/运行态 revision 基线。 */
export interface SessionSyncBaseline {
  entities: Map<string, number>;
  active: number;
}

function targetFromPayload(
  payload: SessionCreatedPayload | SessionActivatedPayload,
): SessionTarget | null {
  const projectId = payload.project_id || payload.session.project_id;
  const agentId = payload.agent_id || payload.session.agent_id;
  const agentName = payload.agent_name || payload.session.agent_name;
  if (!projectId || !agentId || !agentName || !payload.session.session_id) return null;
  return { projectId, agentId, agentName, sessionId: payload.session.session_id };
}

export const useSessionsStore = defineStore("ghrah-sessions", () => {
  const sessions = ref<Map<string, SessionProjection>>(new Map());
  const activeSessions = ref<Map<string, ActiveSessionSelection>>(new Map());
  /** 实体事件 revision：晚到 list 不得覆盖 revision 更高的实体状态。 */
  const entityRevisions = ref<Map<string, number>>(new Map());
  /** Agent 运行态指针 revision：晚到 list 的 active_session_id 不得回退事件后的指针。 */
  const activeRevisions = ref<Map<string, number>>(new Map());
  /** 纯前端展示态：用户正在查看的 Session（不产生运行时副作用）。 */
  const viewedSessions = ref<Map<string, string>>(new Map());

  function bumpEntity(key: string) {
    entityRevisions.value.set(key, (entityRevisions.value.get(key) ?? 0) + 1);
  }

  function bumpActive(target: AgentTarget) {
    const key = agentKey(target);
    activeRevisions.value.set(key, (activeRevisions.value.get(key) ?? 0) + 1);
  }

  /** 请求发出前捕获基线；结果晚到时按 revision 丢弃过期覆盖。 */
  function captureBaseline(target: AgentTarget): SessionSyncBaseline {
    const entities = new Map<string, number>();
    for (const projection of sessionsForAgent(target)) {
      const key = sessionKey(projection.target);
      entities.set(key, entityRevisions.value.get(key) ?? 0);
    }
    return { entities, active: activeRevisions.value.get(agentKey(target)) ?? 0 };
  }

  function sessionsForAgent(target: AgentTarget): SessionProjection[] {
    return [...sessions.value.values()].filter(
      (session) =>
        session.target.projectId === target.projectId && session.target.agentId === target.agentId,
    );
  }

  function getSession(target: SessionTarget): SessionProjection | undefined {
    return sessions.value.get(sessionKey(target));
  }

  function activeSessionId(target: AgentTarget): string | null {
    return activeSessions.value.get(agentKey(target))?.sessionId ?? null;
  }

  function setActiveSession(target: AgentTarget, sessionId: string | null) {
    const next = new Map(activeSessions.value);
    if (sessionId) next.set(agentKey(target), { target, sessionId });
    else next.delete(agentKey(target));
    activeSessions.value = next;
  }

  /** 展示态读取：用户显式选择优先，否则回退运行态（仅初始化语义）。 */
  function viewedSessionId(target: AgentTarget): string | null {
    return viewedSessions.value.get(agentKey(target)) ?? activeSessionId(target);
  }

  function viewSession(target: AgentTarget, sessionId: string | null) {
    const next = new Map(viewedSessions.value);
    if (sessionId) next.set(agentKey(target), sessionId);
    else next.delete(agentKey(target));
    viewedSessions.value = next;
  }

  function upsert(target: SessionTarget, info: SessionInfoPayload) {
    sessions.value.set(sessionKey(target), {
      target,
      info: {
        ...info,
        project_id: target.projectId,
        agent_id: target.agentId,
        agent_name: target.agentName,
      },
    });
  }

  function onSessionCreated(payload: SessionCreatedPayload) {
    const target = targetFromPayload(payload);
    if (!target) return;
    upsert(target, payload.session);
    bumpEntity(sessionKey(target));
  }

  function onSessionActivated(payload: SessionActivatedPayload) {
    const target = targetFromPayload(payload);
    if (!target) return;
    upsert(target, payload.session);
    bumpEntity(sessionKey(target));
    setActiveSession(target, target.sessionId);
    bumpActive(target);
  }

  function removeSession(
    payload: SessionArchivedPayload | SessionDeletedPayload,
  ): SessionTarget | null {
    if (!payload.project_id || !payload.agent_id || !payload.agent_name || !payload.session_id) {
      return null;
    }
    const target: SessionTarget = {
      projectId: payload.project_id,
      agentId: payload.agent_id,
      agentName: payload.agent_name,
      sessionId: payload.session_id,
    };
    const key = sessionKey(target);
    sessions.value.delete(key);
    bumpEntity(key);
    if (activeSessionId(target) === target.sessionId) setActiveSession(target, null);
    if (viewedSessions.value.get(agentKey(target)) === target.sessionId) {
      viewSession(target, null);
    }
    return target;
  }

  function replaceAgentSessions(
    target: AgentTarget,
    list: SessionInfoPayload[],
    options: {
      explicitActiveSessionId?: string | null;
      baseline?: SessionSyncBaseline;
    } = {},
  ) {
    const baseline = options.baseline;
    const next = new Map(
      [...sessions.value.entries()].filter(
        ([, session]) =>
          session.target.projectId !== target.projectId ||
          session.target.agentId !== target.agentId,
      ),
    );
    // 事件已更新的实体（revision 高于请求基线）保留事件版本，list 只补缺失实体。
    if (baseline) {
      for (const [key, projection] of sessions.value.entries()) {
        if (
          projection.target.projectId === target.projectId &&
          projection.target.agentId === target.agentId &&
          (entityRevisions.value.get(key) ?? 0) > (baseline.entities.get(key) ?? 0)
        ) {
          next.set(key, projection);
        }
      }
    }
    for (const info of list) {
      if (!info.session_id) continue;
      const sessionTarget = { ...target, sessionId: info.session_id };
      const key = sessionKey(sessionTarget);
      if (next.has(key)) continue;
      next.set(key, {
        target: sessionTarget,
        info: {
          ...info,
          project_id: target.projectId,
          agent_id: target.agentId,
          agent_name: target.agentName,
        },
      });
    }
    sessions.value = next;
    const activeAdvancedByEvent =
      baseline !== undefined &&
      (activeRevisions.value.get(agentKey(target)) ?? 0) > baseline.active;
    if (options.explicitActiveSessionId !== undefined && !activeAdvancedByEvent) {
      setActiveSession(target, options.explicitActiveSessionId);
    }
    const active = activeSessionId(target);
    if (active && !next.has(sessionKey({ ...target, sessionId: active })))
      setActiveSession(target, null);
  }

  /**
   * 推送路径合并（SESSION_LIST_RESULT 事件）：快照即服务器最新权威，但
   * 离散事件（created/activated/...）已落地的状态视为不早于推送——事件
   * 已写入的实体与 active 指针保留，推送只补缺失实体与未覆盖指针。
   */
  function mergePushedAgentSessions(
    target: AgentTarget,
    list: SessionInfoPayload[],
    explicitActiveSessionId?: string | null,
  ) {
    for (const info of list) {
      if (!info.session_id) continue;
      const sessionTarget = { ...target, sessionId: info.session_id };
      const key = sessionKey(sessionTarget);
      if (sessions.value.has(key)) continue;
      upsert(sessionTarget, info);
    }
    if (explicitActiveSessionId !== undefined) {
      const current = activeSessionId(target);
      if (current === null || !sessions.value.has(sessionKey({ ...target, sessionId: current }))) {
        setActiveSession(target, explicitActiveSessionId);
      }
    }
    const active = activeSessionId(target);
    if (active && !sessions.value.has(sessionKey({ ...target, sessionId: active })))
      setActiveSession(target, null);
  }

  function clearAgent(target: AgentTarget) {
    sessions.value = new Map(
      [...sessions.value.entries()].filter(
        ([, session]) =>
          session.target.projectId !== target.projectId ||
          session.target.agentId !== target.agentId,
      ),
    );
    setActiveSession(target, null);
    viewedSessions.value = new Map(
      [...viewedSessions.value.entries()].filter(([key]) => key !== agentKey(target)),
    );
  }

  function clearProject(projectId: string) {
    sessions.value = new Map(
      [...sessions.value.entries()].filter(([, session]) => session.target.projectId !== projectId),
    );
    activeSessions.value = new Map(
      [...activeSessions.value.entries()].filter(
        ([, selection]) => selection.target.projectId !== projectId,
      ),
    );
    viewedSessions.value = new Map(
      [...viewedSessions.value.entries()].filter(([key]) => {
        try {
          const [parsedProject] = JSON.parse(key) as [string, string];
          return parsedProject !== projectId;
        } catch {
          return true;
        }
      }),
    );
  }

  function clearAll() {
    sessions.value = new Map();
    activeSessions.value = new Map();
    viewedSessions.value = new Map();
  }

  return {
    sessions,
    activeSessions,
    sessionsForAgent,
    getSession,
    activeSessionId,
    setActiveSession,
    viewedSessionId,
    viewSession,
    captureBaseline,
    onSessionCreated,
    onSessionActivated,
    removeSession,
    replaceAgentSessions,
    mergePushedAgentSessions,
    clearAgent,
    clearProject,
    clearAll,
  };
});
