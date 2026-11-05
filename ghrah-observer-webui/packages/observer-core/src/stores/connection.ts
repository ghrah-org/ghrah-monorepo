// SPDX-FileCopyrightText: 2026 chenxya <chenxya@ghrah.org>
//
// SPDX-License-Identifier: Apache-2.0

import { defineStore } from "pinia";
import { computed, ref } from "vue";

import { deriveHttpBase } from "../http.js";

export type ConnectionState = "disconnected" | "connecting" | "connected" | "reconnecting";

export const useConnectionStore = defineStore("ghrah-connection", () => {
  const state = ref<ConnectionState>("disconnected");
  const serverUrl = ref(
    (import.meta.env?.VITE_GHRAH_SUBJECT_WS_URL as string | undefined) ??
      (globalThis as { __GHRAH_SUBJECT_WS_URL__?: string }).__GHRAH_SUBJECT_WS_URL__ ??
      "ws://localhost:4112/ws",
  );
  const apiToken = ref<string | undefined>(
    (import.meta.env?.VITE_GHRAH_SUBJECT_HTTP_API_TOKEN as string | undefined) ??
      (globalThis as { __GHRAH_SUBJECT_HTTP_API_TOKEN__?: string })
        .__GHRAH_SUBJECT_HTTP_API_TOKEN__,
  );
  /** 管理面 HTTP base 从 WS 地址派生；宿主可另行覆盖。 */
  const httpBaseUrl = computed(() => deriveHttpBase(serverUrl.value));

  function setConnected() {
    state.value = "connected";
  }

  function setDisconnected() {
    state.value = "disconnected";
  }

  function setConnecting() {
    state.value = "connecting";
  }

  function setReconnecting() {
    state.value = "reconnecting";
  }

  function setServerUrl(url: string) {
    serverUrl.value = url;
  }

  function setApiToken(token: string | undefined) {
    apiToken.value = token;
  }

  return {
    state,
    serverUrl,
    httpBaseUrl,
    apiToken,
    setConnected,
    setDisconnected,
    setConnecting,
    setReconnecting,
    setServerUrl,
    setApiToken,
  };
});
