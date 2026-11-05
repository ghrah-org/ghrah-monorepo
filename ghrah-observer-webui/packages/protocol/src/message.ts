// SPDX-FileCopyrightText: 2026 chenxya <chenxya@ghrah.org>
//
// SPDX-License-Identifier: Apache-2.0

import { z } from "zod";
import { ClientType } from "./enums.js";

export const ServerMessageSchema = z.object({
  type: z.string(),
  payload: z.record(z.unknown()).optional().default({}),
  request_id: z.string().nullable().optional(),
  timestamp: z.number().nullable().optional(),
  client_type: z.nativeEnum(ClientType).nullable().optional(),
  seq_id: z.number().int().nullable().optional(),
});

export type ServerMessage = z.infer<typeof ServerMessageSchema>;

export function serializeMessage(message: ServerMessage): string {
  const data = { ...message };
  if (data.timestamp == null) {
    data.timestamp = Date.now() / 1000;
  }
  return JSON.stringify(data);
}

export function parseMessage(raw: string): ServerMessage {
  return ServerMessageSchema.parse(JSON.parse(raw));
}
