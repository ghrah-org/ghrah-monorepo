// SPDX-FileCopyrightText: 2026 chenxya <chenxya@ghrah.org>
//
// SPDX-License-Identifier: Apache-2.0

import { createPinia, setActivePinia } from "pinia";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import { useConnectionStore } from "./connection.js";

describe("useConnectionStore", () => {
  beforeEach(() => {
    setActivePinia(createPinia());
  });

  afterEach(() => {
    vi.unstubAllGlobals();
    vi.unstubAllEnvs();
  });

  it("initial state is disconnected", () => {
    const store = useConnectionStore();
    expect(store.state).toBe("disconnected");
  });

  it("default serverUrl is ws://localhost:4112/ws", () => {
    const store = useConnectionStore();
    expect(store.serverUrl).toBe("ws://localhost:4112/ws");
  });

  it("falls back to injected global WS URL when env is unset", () => {
    vi.stubGlobal("__GHRAH_SUBJECT_WS_URL__", "ws://127.0.0.1:4222/ws");
    const store = useConnectionStore();
    expect(store.serverUrl).toBe("ws://127.0.0.1:4222/ws");
  });

  it("env var wins over injected global", () => {
    vi.stubEnv("VITE_GHRAH_SUBJECT_WS_URL", "ws://env:4113/ws");
    vi.stubGlobal("__GHRAH_SUBJECT_WS_URL__", "ws://injected:4222/ws");
    const store = useConnectionStore();
    expect(store.serverUrl).toBe("ws://env:4113/ws");
  });

  it("setConnected changes state to connected", () => {
    const store = useConnectionStore();
    store.setConnected();
    expect(store.state).toBe("connected");
  });

  it("setDisconnected changes state to disconnected", () => {
    const store = useConnectionStore();
    store.setConnected();
    store.setDisconnected();
    expect(store.state).toBe("disconnected");
  });

  it("setConnecting changes state to connecting", () => {
    const store = useConnectionStore();
    store.setConnecting();
    expect(store.state).toBe("connecting");
  });

  it("setReconnecting changes state to reconnecting", () => {
    const store = useConnectionStore();
    store.setReconnecting();
    expect(store.state).toBe("reconnecting");
  });

  it("derives the HTTP base from the WS URL until a server URL is chosen", () => {
    const store = useConnectionStore();
    expect(store.httpBaseUrl).toBe("http://localhost:4112");
    store.setServerUrl("ws://other:9999/ws");
    expect(store.httpBaseUrl).toBe("http://other:9999");
  });

  it("uses an explicit HTTP base when the host configures one", () => {
    vi.stubEnv("VITE_GHRAH_SUBJECT_HTTP_URL", "http://localhost:4114/");
    const store = useConnectionStore();
    expect(store.httpBaseUrl).toBe("http://localhost:4114");
  });

  it("setServerUrl updates the url", () => {
    const store = useConnectionStore();
    store.setServerUrl("ws://other:9999/ws");
    expect(store.serverUrl).toBe("ws://other:9999/ws");
  });

  it("state transitions work in sequence", () => {
    const store = useConnectionStore();
    expect(store.state).toBe("disconnected");

    store.setConnecting();
    expect(store.state).toBe("connecting");

    store.setConnected();
    expect(store.state).toBe("connected");

    store.setDisconnected();
    expect(store.state).toBe("disconnected");

    store.setReconnecting();
    expect(store.state).toBe("reconnecting");
  });
});
