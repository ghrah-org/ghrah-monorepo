// SPDX-FileCopyrightText: 2026 chenxya <chenxya@ghrah.org>
//
// SPDX-License-Identifier: Apache-2.0

/**
 * 内容块兼容层（M2 协议就绪后的薄适配）。
 *
 * `@ghrah/protocol` 的 `ContentBlockSchema` / `InputContentBlockSchema` 已包含
 * `video` 与 `attachment_id`；观察端统一用 `ChatBlock`（= 协议 ContentBlock）
 * 承载 Room 回读、pending 与渲染，避免投影层与聊天层各自维护判别联合。
 */

import { InputContentBlockSchema } from "@ghrah/protocol";
import type { ContentBlock, VideoBlockPayload } from "@ghrah/protocol";

/** M2 `video.frames[]` 的 image 帧（协议结构别名）。 */
export type VideoFrameBlockLike = NonNullable<VideoBlockPayload["frames"]>[number];

/** M2 `video` 块（协议结构别名）。 */
export type VideoBlockLike = VideoBlockPayload;

export type ChatBlock = ContentBlock;

/** 解析一个 RoomLog/消息块：结果侧与输入侧的公共子集，非法项返回 null。 */
export function parseChatBlock(item: unknown): ChatBlock | null {
  const parsed = InputContentBlockSchema.safeParse(item);
  return parsed.success ? parsed.data : null;
}

/** 兼容旧调用点：只接受 video 块。 */
export function parseVideoBlock(item: unknown): VideoBlockLike | null {
  const parsed = parseChatBlock(item);
  return parsed?.type === "video" ? parsed : null;
}

export function isVideoBlock(block: ChatBlock): block is Extract<ChatBlock, { type: "video" }> {
  return block.type === "video";
}

/** 从块里取可见正文（仅 text 块）。 */
export function textFromChatBlocks(blocks: ChatBlock[] | undefined): string {
  if (!blocks) return "";
  const parts: string[] = [];
  for (const block of blocks) {
    if (block.type === "text") parts.push(block.text);
  }
  return parts.join(" ").trim();
}
