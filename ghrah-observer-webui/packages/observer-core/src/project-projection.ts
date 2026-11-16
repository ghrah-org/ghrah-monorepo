// SPDX-FileCopyrightText: 2026 chenxya <chenxya@ghrah.org>
//
// SPDX-License-Identifier: Apache-2.0

/** Project REST 响应的 store 投影。
 *
 * WS 路径由 ``bind.ts`` 的 ``command_result``/事件分支更新 store；REST 响应
 * 不会产生 WS 消息，因此适配层必须显式做同样的投影。这里把「列表替换 +
 * 移除项派生清理」抽出来，由 WS 与 REST 两条路径共用，避免语义漂移。
 */

import type { ProjectInfoPayload } from "@ghrah/protocol";

import { clearProjectDerived } from "./clear-derived.js";
import { useProjectsStore } from "./stores/projects.js";

/** 应用 ``project_list`` 结果（与 bind.ts 原分支逐行同义）。 */
export function applyProjectListResult(
  data: { readonly projects?: readonly ProjectInfoPayload[] },
  archived: boolean,
): void {
  const projects = useProjectsStore();
  const list = data.projects ?? [];
  const previous = new Set(
    (archived ? projects.archivedProjectList : projects.projectList).map(
      (project) => project.project_id,
    ),
  );
  projects.replaceProjects([...list], archived);
  const retained = new Set(
    list
      .filter((project) => (project.archived_at != null) === archived)
      .map((project) => project.project_id),
  );
  for (const projectId of previous) {
    if (!retained.has(projectId)) clearProjectDerived(projectId);
  }
}

/** 写操作返回的单项目视图：与事件投影同义的 upsert（含归档后清 active）。 */
export function applyProjectUpsert(project: ProjectInfoPayload): void {
  useProjectsStore().onProjectEvent({ project });
}

/** 删除结果：先清派生，再从项目表移除（与 bind.ts 的 PROJECT_DELETED 顺序一致）。 */
export function applyProjectRemoved(projectId: string): void {
  clearProjectDerived(projectId);
  useProjectsStore().removeProject(projectId);
}
