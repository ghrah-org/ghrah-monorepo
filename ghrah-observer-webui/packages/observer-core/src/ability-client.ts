// SPDX-FileCopyrightText: 2026 chenxya <chenxya@ghrah.org>
//
// SPDX-License-Identifier: Apache-2.0

import { createAbilityControlClient, type AbilityControlClient } from "./ability-control.js";
import { HttpClient } from "./http.js";
import { useConnectionStore } from "./stores/connection.js";

export type AbilityControlClientFactory = (projectId: string) => AbilityControlClient;

let factory: AbilityControlClientFactory | null = null;

/** 测试注入管理面客户端。传 null 恢复为按连接地址创建。 */
export function setAbilityControlClientFactory(next: AbilityControlClientFactory | null): void {
  factory = next;
}

/**
 * 按项目创建管理面客户端。
 * `status()` 不使用项目路径；调用方仍须传入通过标识校验的项目 id。
 */
export function abilityControlClient(projectId: string): AbilityControlClient {
  if (factory) return factory(projectId);
  const connection = useConnectionStore();
  const http = new HttpClient({
    baseUrl: connection.httpBaseUrl,
    token: connection.apiToken,
  });
  return createAbilityControlClient(http, { projectId });
}
