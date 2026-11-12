// SPDX-FileCopyrightText: 2026 chenxya <chenxya@ghrah.org>
//
// SPDX-License-Identifier: Apache-2.0

import { z } from "zod";

/**
 * 输入内容块（消息正文里的结构化内容）。
 *
 * 与 Python `ghrah.protocol.payloads.content` 一一对应：text / image / audio / file / video。
 * 工具调用、工具结果、错误等「结果侧」块不在此列，见 agent.ts 的 ContentBlockSchema。
 * `attachment_id` 指向 Subject 附件库（sha256 hex）；字节永不进入消息正文。
 */
export const TextBlockPayloadSchema = z.object({
  type: z.literal("text"),
  text: z.string(),
});

export const ImageBlockPayloadSchema = z.object({
  type: z.literal("image"),
  url: z.string().nullable().optional(),
  base64: z.string().nullable().optional(),
  attachment_id: z.string().nullable().optional(),
  mime_type: z.string().nullable().optional(),
});

export const AudioBlockPayloadSchema = z.object({
  type: z.literal("audio"),
  data: z.string(),
  attachment_id: z.string().nullable().optional(),
  mime_type: z.string(),
});

export const FileBlockPayloadSchema = z.object({
  type: z.literal("file"),
  url: z.string().nullable().optional(),
  base64: z.string().nullable().optional(),
  attachment_id: z.string().nullable().optional(),
  mime_type: z.string().nullable().optional(),
  filename: z.string().nullable().optional(),
});

export const VideoBlockPayloadSchema = z.object({
  type: z.literal("video"),
  url: z.string().nullable().optional(),
  base64: z.string().nullable().optional(),
  attachment_id: z.string().nullable().optional(),
  mime_type: z.string().nullable().optional(),
  filename: z.string().nullable().optional(),
  duration_s: z.number().nullable().optional(),
  width: z.number().int().nullable().optional(),
  height: z.number().int().nullable().optional(),
  frames: z.array(ImageBlockPayloadSchema).nullable().optional(),
});

export const InputContentBlockSchema = z.discriminatedUnion("type", [
  TextBlockPayloadSchema,
  ImageBlockPayloadSchema,
  AudioBlockPayloadSchema,
  FileBlockPayloadSchema,
  VideoBlockPayloadSchema,
]);

export type TextBlockPayload = z.infer<typeof TextBlockPayloadSchema>;
export type ImageBlockPayload = z.infer<typeof ImageBlockPayloadSchema>;
export type AudioBlockPayload = z.infer<typeof AudioBlockPayloadSchema>;
export type FileBlockPayload = z.infer<typeof FileBlockPayloadSchema>;
export type VideoBlockPayload = z.infer<typeof VideoBlockPayloadSchema>;
export type InputContentBlock = z.infer<typeof InputContentBlockSchema>;
