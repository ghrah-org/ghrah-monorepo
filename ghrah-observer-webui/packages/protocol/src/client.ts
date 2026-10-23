import { generateRequestId } from "./builders.js";
import { CommandType, EventType, SystemType } from "./enums.js";
import type { ServerMessage } from "./message.js";
import { parseMessage, serializeMessage } from "./message.js";
import type { CommandResultPayload, SubscribePayload } from "./payloads.js";

type EventHandler = (message: ServerMessage) => void;

interface Deferred<T> {
  promise: Promise<T>;
  resolve: (value: T) => void;
  reject: (reason: unknown) => void;
}

function createDeferred<T>(): Deferred<T> {
  let resolve!: (value: T) => void;
  let reject!: (reason: unknown) => void;
  const promise = new Promise<T>((res, rej) => {
    resolve = res;
    reject = rej;
  });
  return { promise, resolve, reject };
}

const WS_OPEN = 1;

export type WebSocketLike = {
  readyState: number;
  send(data: string): void;
  close(): void;
  onopen: ((this: WebSocketLike) => void) | null;
  onmessage: ((this: WebSocketLike, event: { data: string }) => void) | null;
  onclose: ((this: WebSocketLike) => void) | null;
  onerror: ((this: WebSocketLike, err: Event) => void) | null;
};

export type WebSocketFactory = (url: string) => WebSocketLike;

function defaultWebSocketFactory(url: string): WebSocketLike {
  return new WebSocket(url) as unknown as WebSocketLike;
}

export class ServerClient {
  protected _ws: WebSocketLike | null = null;
  protected _running = false;
  protected _seqId = 0;
  protected _lastSeqId = 0;
  protected _reconnectAttempt = 0;
  protected _maxReconnectDelay: number;
  protected _initialReconnectDelay: number;
  protected _wsFactory: WebSocketFactory;

  protected _eventHandlers = new Map<string, Set<EventHandler>>();
  protected _systemHandlers = new Map<string, Set<EventHandler>>();
  protected _pendingRequests = new Map<string, Deferred<CommandResultPayload>>();
  protected _requestCommandTypes = new Map<string, string>();

  protected _subscriptions: SubscribePayload[] = [];
  protected _foldedState = new Map<string, Record<string, unknown>>();
  protected readonly _clientId: string;

  protected _onConnectedCallbacks: Array<() => void> = [];
  protected _onDisconnectedCallbacks: Array<() => void> = [];
  protected _onReconnectingCallbacks: Array<() => void> = [];
  protected _onReconnectedCallbacks: Array<() => void> = [];

  private _heartbeatTimer: ReturnType<typeof setInterval> | null = null;

  constructor(
    protected _serverUrl: string,
    protected _clientType: string = "observer",
    opts?: {
      maxReconnectDelay?: number;
      initialReconnectDelay?: number;
      wsFactory?: WebSocketFactory;
    },
  ) {
    this._maxReconnectDelay = opts?.maxReconnectDelay ?? 60_000;
    this._initialReconnectDelay = opts?.initialReconnectDelay ?? 1_000;
    this._wsFactory = opts?.wsFactory ?? defaultWebSocketFactory;
    this._clientId =
      typeof crypto !== "undefined" && crypto.randomUUID
        ? crypto.randomUUID()
        : `${Date.now()}-${Math.random().toString(36).slice(2)}`;
  }

  get connected(): boolean {
    return this._ws !== null && this._ws.readyState === WS_OPEN && this._running;
  }

  get lastSeqId(): number {
    return this._lastSeqId;
  }

  get foldedState(): Record<string, Record<string, unknown>> {
    const result: Record<string, Record<string, unknown>> = {};
    for (const [key, value] of this._foldedState) {
      result[key] = value;
    }
    return result;
  }

  async connect(): Promise<void> {
    const url = this._buildUrl();
    this._running = true;
    await this._doConnect(url);
    await this._syncInitialState();
  }

  async disconnect(): Promise<void> {
    this._running = false;
    this._clearHeartbeat();
    if (this._ws !== null) {
      this._ws.close();
      this._ws = null;
    }
  }

  async send(message: ServerMessage): Promise<void> {
    if (this._ws === null || this._ws.readyState !== WS_OPEN) {
      throw new ConnectionError("Not connected to server");
    }
    this._ws.send(serializeMessage(message));
  }

  async request(message: ServerMessage, timeout = 30_000): Promise<CommandResultPayload> {
    const requestId = message.request_id ?? generateRequestId();
    message.request_id = requestId;
    this._requestCommandTypes.set(requestId, message.type);

    const deferred = createDeferred<CommandResultPayload>();
    this._pendingRequests.set(requestId, deferred);

    const timer = setTimeout(() => {
      this._pendingRequests.delete(requestId);
      this._requestCommandTypes.delete(requestId);
      deferred.reject(new TimeoutError(`Request ${requestId} timed out after ${timeout}ms`));
    }, timeout);

    try {
      await this.send(message);
      const result = await deferred.promise;
      return result;
    } finally {
      clearTimeout(timer);
      this._pendingRequests.delete(requestId);
      this._requestCommandTypes.delete(requestId);
    }
  }

  on(eventType: string, handler: EventHandler): void {
    const target = this._isSystemType(eventType) ? this._systemHandlers : this._eventHandlers;
    if (!target.has(eventType)) {
      target.set(eventType, new Set());
    }
    target.get(eventType)!.add(handler);
  }

  off(eventType: string, handler?: EventHandler): void {
    const target = this._isSystemType(eventType) ? this._systemHandlers : this._eventHandlers;
    if (handler) {
      target.get(eventType)?.delete(handler);
    } else {
      target.delete(eventType);
    }
  }

  onConnected(callback: () => void): void {
    this._onConnectedCallbacks.push(callback);
  }

  onDisconnected(callback: () => void): void {
    this._onDisconnectedCallbacks.push(callback);
  }

  onReconnecting(callback: () => void): void {
    this._onReconnectingCallbacks.push(callback);
  }

  onReconnected(callback: () => void): void {
    this._onReconnectedCallbacks.push(callback);
  }

  clearLifecycleCallbacks(): void {
    this._onConnectedCallbacks.length = 0;
    this._onDisconnectedCallbacks.length = 0;
    this._onReconnectingCallbacks.length = 0;
    this._onReconnectedCallbacks.length = 0;
  }

  protected _buildUrl(): string {
    const sep = this._serverUrl.includes("?") ? "&" : "?";
    let url = `${this._serverUrl}${sep}client_type=${this._clientType}&client_id=${this._clientId}`;
    if (this._lastSeqId > 0) {
      url += `&last_seq_id=${this._lastSeqId}`;
    }
    return url;
  }

  protected _resetConnectionState(): void {
    this._seqId = 0;
  }

  protected _reconnectDelay(): number {
    const delay = Math.min(
      this._initialReconnectDelay * 2 ** (this._reconnectAttempt % 10),
      this._maxReconnectDelay,
    );
    const jitter = Math.random() * delay * 0.5;
    return delay + jitter;
  }

  protected async _doConnect(url: string): Promise<void> {
    return new Promise((resolve, reject) => {
      const ws = this._wsFactory(url);

      ws.onopen = () => {
        this._ws = ws;
        this._resetConnectionState();
        this._startHeartbeat();
        const isReconnect = this._reconnectAttempt > 0;
        if (isReconnect) {
          this._notifyReconnected();
        } else {
          this._notifyConnected();
        }
        this._reconnectAttempt = 0;
        resolve();
      };

      ws.onmessage = (event) => {
        this._processMessage(event.data);
      };

      ws.onclose = () => {
        this._ws = null;
        this._clearHeartbeat();
        this._notifyDisconnected();
        if (this._running) {
          this._scheduleReconnect();
        }
      };

      ws.onerror = () => {
        if (this._ws === null || this._ws.readyState !== WS_OPEN) {
          reject(new ConnectionError("Failed to connect to server"));
        }
      };
    });
  }

  protected async _scheduleReconnect(): Promise<void> {
    this._reconnectAttempt++;
    const delay = this._reconnectDelay();
    this._notifyReconnecting();
    await new Promise((r) => setTimeout(r, delay));
    if (!this._running) return;
    try {
      const url = this._buildUrl();
      await this._doConnect(url);
      await this._resubscribe();
      await this._syncInitialState();
    } catch {
      this._scheduleReconnect();
    }
  }

  protected async _resubscribe(): Promise<void> {
    for (const sub of this._subscriptions) {
      const msg: ServerMessage = {
        type: CommandType.SUBSCRIBE,
        payload: sub as Record<string, unknown>,
        request_id: generateRequestId(),
        client_type: this._clientType as ServerMessage["client_type"],
      };
      await this.send(msg);
    }
  }

  protected async _syncInitialState(): Promise<void> {}

  protected _processMessage(raw: string): void {
    const message = parseMessage(raw);
    this._seqId++;
    if (message.seq_id != null) {
      this._lastSeqId = message.seq_id;
    }

    const msgType = message.type;

    if (msgType === SystemType.PING) {
      this._handlePing();
      return;
    }

    if (msgType === SystemType.COMMAND_RESULT) {
      const payload = message.payload as CommandResultPayload;
      const requestId =
        ((payload as Record<string, unknown>).request_id as string | undefined) ||
        message.request_id;
      if (requestId) {
        const originalCommand = this._requestCommandTypes.get(requestId);
        if (originalCommand) {
          (payload as Record<string, unknown>).original_command = originalCommand;
        }
        const deferred = this._pendingRequests.get(requestId);
        if (deferred) {
          this._pendingRequests.delete(requestId);
          this._requestCommandTypes.delete(requestId);
          (payload as Record<string, unknown>).request_id = requestId;
          deferred.resolve(payload);
        }
      }
      this._dispatch(msgType, message);
      return;
    }

    if (msgType === SystemType.ERROR && message.request_id) {
      const deferred = this._pendingRequests.get(message.request_id);
      if (deferred) {
        const payload = message.payload as { code: string; message: string };
        this._pendingRequests.delete(message.request_id);
        this._requestCommandTypes.delete(message.request_id);
        deferred.resolve({
          request_id: message.request_id,
          success: false,
          error: payload.message,
          error_detail: payload.code,
        });
      }
    }

    this._applyStateFold(message);
    this._dispatch(msgType, message);
  }

  protected _handlePing(): void {
    try {
      if (this._ws?.readyState === WS_OPEN) {
        this._ws.send(serializeMessage({ type: SystemType.PONG, payload: {} }));
      }
    } catch {
      // ignore
    }
  }

  protected _applyStateFold(message: ServerMessage): void {
    const msgType = message.type;
    const eventValues = Object.values(EventType) as string[];
    if (!eventValues.includes(msgType)) return;

    const payload = message.payload;
    const agentName =
      (payload["agent_name"] as string) ??
      (payload["name"] as string) ??
      (payload["sender"] as string) ??
      "";
    if (!agentName) return;

    const foldKey = `${agentName}:${msgType}`;
    this._foldedState.set(foldKey, payload);
  }

  protected _dispatch(msgType: string, message: ServerMessage): void {
    const handlers = this._eventHandlers.get(msgType) ?? this._systemHandlers.get(msgType);
    if (handlers) {
      for (const handler of handlers) {
        try {
          handler(message);
        } catch {
          // handler errors are swallowed to avoid breaking dispatch
        }
      }
    }
  }

  protected _notifyConnected(): void {
    for (const cb of this._onConnectedCallbacks) cb();
  }

  protected _notifyDisconnected(): void {
    for (const cb of this._onDisconnectedCallbacks) cb();
  }

  protected _notifyReconnecting(): void {
    for (const cb of this._onReconnectingCallbacks) cb();
  }

  protected _notifyReconnected(): void {
    for (const cb of this._onReconnectedCallbacks) cb();
  }

  protected _startHeartbeat(): void {
    this._clearHeartbeat();
    this._heartbeatTimer = setInterval(() => {
      if (this._ws?.readyState === WS_OPEN) {
        this._ws.send(serializeMessage({ type: SystemType.PING, payload: {} }));
      }
    }, 30_000);
  }

  protected _clearHeartbeat(): void {
    if (this._heartbeatTimer != null) {
      clearInterval(this._heartbeatTimer);
      this._heartbeatTimer = null;
    }
  }

  private static readonly SYSTEM_TYPES = new Set<string>(Object.values(SystemType));

  private _isSystemType(type: string): boolean {
    return ServerClient.SYSTEM_TYPES.has(type);
  }
}

export class ConnectionError extends Error {
  constructor(message: string) {
    super(message);
    this.name = "ConnectionError";
  }
}

export class TimeoutError extends Error {
  constructor(message: string) {
    super(message);
    this.name = "TimeoutError";
  }
}
