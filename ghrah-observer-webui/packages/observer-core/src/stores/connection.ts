// SPDX-FileCopyrightText: 2026 chenxya <chenxya@ghrah.org>
//
// SPDX-License-Identifier: Apache-2.0

import { defineStore } from "pinia";
import { ref } from "vue";

export type ConnectionState = "disconnected" | "connecting" | "connected" | "reconnecting";

export const useConnectionStore = defineStore("ghrah-connection", () => {
  const state = ref<ConnectionState>("disconnected");
  const serverUrl = ref(
    (import.meta.env?.VITE_GHRAH_SUBJECT_WS_URL as string | undefined) ??
      (globalThis as { __GHRAH_SUBJECT_WS_URL__?: string }).__GHRAH_SUBJECT_WS_URL__ ??
      "ws://localhost:4112/ws",
  );

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

  return {
    state,
    serverUrl,
    setConnected,
    setDisconnected,
    setConnecting,
    setReconnecting,
    setServerUrl,
  };
});
