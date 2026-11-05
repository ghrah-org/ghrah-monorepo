// SPDX-FileCopyrightText: 2026 chenxya <chenxya@ghrah.org>
//
// SPDX-License-Identifier: Apache-2.0

import { ClientType, type CommandType, type EventType, SystemType } from "./enums.js";
import type { ServerMessage } from "./message.js";

export function generateRequestId(): string {
  return crypto.randomUUID().replace(/-/g, "").slice(0, 12);
}

export function createCommand(
  commandType: CommandType,
  payload: Record<string, unknown>,
  requestId?: string,
): ServerMessage {
  return {
    type: commandType,
    payload,
    request_id: requestId ?? generateRequestId(),
    client_type: ClientType.OBSERVER,
  };
}

export function createEvent(eventType: EventType, payload: Record<string, unknown>): ServerMessage {
  return {
    type: eventType,
    payload,
  };
}

export function createCommandResult(
  requestId: string,
  success: boolean,
  data?: unknown,
  error?: string,
  errorDetail?: string,
): ServerMessage {
  return {
    type: SystemType.COMMAND_RESULT,
    payload: {
      request_id: requestId,
      success,
      data: data ?? null,
      error: error ?? null,
      error_detail: errorDetail ?? null,
    },
    request_id: requestId,
  };
}

export function createError(
  code: string,
  message: string,
  details?: Record<string, unknown>,
  requestId?: string,
): ServerMessage {
  return {
    type: SystemType.ERROR,
    payload: {
      code,
      message,
      details: details ?? null,
    },
    request_id: requestId ?? null,
  };
}

export function createPing(): ServerMessage {
  return { type: SystemType.PING, payload: {} };
}

export function createPong(): ServerMessage {
  return { type: SystemType.PONG, payload: {} };
}
