// SPDX-FileCopyrightText: 2026 chenxya <chenxya@ghrah.org>
//
// SPDX-License-Identifier: Apache-2.0

import type { ContentBlock } from "@ghrah/protocol";
import { computed, ref } from "vue";

/** M1 限额（与 Subject 校验同源；后续 K4 接配置切片后再迁移）。 */
export const MAX_IMAGE_BLOCKS = 4;
export const MAX_TOTAL_BLOCKS = 8;
/** 单块 base64 字符串上限（4 MiB）。 */
export const MAX_IMAGE_BASE64_CHARS = 4 * 1024 * 1024;
/** 单消息 base64 字符串总上限（8 MiB）。 */
export const MAX_TOTAL_BASE64_CHARS = 8 * 1024 * 1024;

export const SUPPORTED_IMAGE_TYPES = [
  "image/png",
  "image/jpeg",
  "image/webp",
  "image/gif",
] as const;

export type AttachmentErrorKey =
  | "chat.attachment.unsupported"
  | "chat.attachment.tooLarge"
  | "chat.attachment.totalTooLarge"
  | "chat.attachment.failed";

export interface ImageAttachment {
  id: string;
  /** 原始文件名（展示用）。 */
  name: string;
  /** 压缩后实际发送的 MIME。 */
  mime: string;
  /** 原始文件字节数（展示用）。 */
  size: number;
  /** 压缩后的 base64（无 data: 前缀）。 */
  base64: string;
  /** 同样内容的 data URL，直接用作缩略图。 */
  dataUrl: string;
}

export interface AttachmentDeps {
  /** 读取并压缩为 data URL；默认实现用 FileReader + canvas。 */
  compress?: (file: File, maxChars: number) => Promise<string>;
  createId?: () => string;
}

class AttachmentReadError extends Error {
  constructor(readonly key: AttachmentErrorKey) {
    super(key);
    this.name = "AttachmentReadError";
  }
}

export function base64FromDataUrl(dataUrl: string): string {
  const comma = dataUrl.indexOf(",");
  return comma >= 0 ? dataUrl.slice(comma + 1) : "";
}

export function mimeFromDataUrl(dataUrl: string): string {
  const match = /^data:([^;,]+)[;,]/.exec(dataUrl);
  return match?.[1] ?? "";
}

export function isSupportedImageType(mime: string): boolean {
  return (SUPPORTED_IMAGE_TYPES as readonly string[]).includes(mime);
}

/** 浏览器默认实现：FileReader 读 data URL；超限时用 canvas 逐级缩放/降质。 */
async function readFileAsDataUrl(file: File): Promise<string> {
  return new Promise<string>((resolve, reject) => {
    const reader = new FileReader();
    reader.onerror = () => reject(new AttachmentReadError("chat.attachment.failed"));
    reader.onload = () => {
      const result = reader.result;
      if (typeof result === "string") resolve(result);
      else reject(new AttachmentReadError("chat.attachment.failed"));
    };
    reader.readAsDataURL(file);
  });
}

type DecodedImage = {
  width: number;
  height: number;
  source: CanvasImageSource;
  release: () => void;
};

async function decodeImage(dataUrl: string): Promise<DecodedImage> {
  if (typeof createImageBitmap === "function") {
    const response = await fetch(dataUrl);
    const blob = await response.blob();
    const bitmap = await createImageBitmap(blob);
    return {
      width: bitmap.width,
      height: bitmap.height,
      source: bitmap,
      release: () => bitmap.close(),
    };
  }
  const image = new Image();
  await new Promise<void>((resolve, reject) => {
    image.onload = () => resolve();
    image.onerror = () => reject(new AttachmentReadError("chat.attachment.failed"));
    image.src = dataUrl;
  });
  return {
    width: image.naturalWidth,
    height: image.naturalHeight,
    source: image,
    release: () => undefined,
  };
}

async function defaultCompressImage(file: File, maxChars: number): Promise<string> {
  const original = await readFileAsDataUrl(file);
  if (base64FromDataUrl(original).length <= maxChars) return original;
  // GIF 缩放/重编码会丢动画，超限直接拒绝而不是静默改成单帧。
  if (file.type === "image/gif") throw new AttachmentReadError("chat.attachment.tooLarge");
  const decoded = await decodeImage(original);
  try {
    let scale = Math.min(1, 2048 / Math.max(decoded.width, decoded.height));
    for (let attempt = 0; attempt < 8; attempt += 1) {
      const canvas = document.createElement("canvas");
      canvas.width = Math.max(1, Math.round(decoded.width * scale));
      canvas.height = Math.max(1, Math.round(decoded.height * scale));
      const context = canvas.getContext("2d");
      if (!context) throw new AttachmentReadError("chat.attachment.failed");
      context.drawImage(decoded.source, 0, 0, canvas.width, canvas.height);
      for (const quality of [0.85, 0.7, 0.55, 0.4]) {
        const webp = canvas.toDataURL("image/webp", quality);
        const encoded = webp.startsWith("data:image/webp")
          ? webp
          : canvas.toDataURL("image/jpeg", quality);
        if (base64FromDataUrl(encoded).length <= maxChars) return encoded;
      }
      scale *= 0.75;
    }
  } finally {
    decoded.release();
  }
  throw new AttachmentReadError("chat.attachment.tooLarge");
}

function errorKey(error: unknown): AttachmentErrorKey {
  return error instanceof AttachmentReadError ? error.key : "chat.attachment.failed";
}

/**
 * composer 附件状态：三入口（文件选择 / 粘贴 / 拖拽）都归到 addFiles。
 * 只做客户端预检与压缩；服务端校验仍是权威，失败由发送回执体现。
 */
export function useAttachments(deps: AttachmentDeps = {}) {
  const items = ref<ImageAttachment[]>([]);
  const errors = ref<AttachmentErrorKey[]>([]);
  const busy = ref(false);
  const compress = deps.compress ?? defaultCompressImage;
  const createId =
    deps.createId ??
    (() => `att-${Date.now().toString(36)}-${Math.random().toString(36).slice(2, 8)}`);

  const blocks = computed<ContentBlock[]>(() =>
    items.value.map((item) => ({
      type: "image" as const,
      base64: item.base64,
      mime_type: item.mime,
    })),
  );
  const hasAttachments = computed(() => items.value.length > 0);
  const totalBase64Chars = computed(() =>
    items.value.reduce((sum, item) => sum + item.base64.length, 0),
  );

  function pushError(key: AttachmentErrorKey) {
    if (errors.value.includes(key)) return;
    errors.value = [...errors.value, key];
  }

  function clearErrors() {
    if (errors.value.length > 0) errors.value = [];
  }

  function remove(id: string) {
    items.value = items.value.filter((item) => item.id !== id);
  }

  function clear() {
    items.value = [];
    errors.value = [];
  }

  async function addFiles(files: Iterable<File>) {
    const incoming = Array.from(files);
    if (incoming.length === 0) return;
    busy.value = true;
    try {
      for (const file of incoming) {
        if (!isSupportedImageType(file.type)) {
          pushError("chat.attachment.unsupported");
          continue;
        }
        if (items.value.length >= MAX_IMAGE_BLOCKS) {
          pushError("chat.attachment.totalTooLarge");
          break;
        }
        let dataUrl: string;
        try {
          dataUrl = await compress(file, MAX_IMAGE_BASE64_CHARS);
        } catch (error) {
          pushError(errorKey(error));
          continue;
        }
        const base64 = base64FromDataUrl(dataUrl);
        if (!base64) {
          pushError("chat.attachment.failed");
          continue;
        }
        if (base64.length > MAX_IMAGE_BASE64_CHARS) {
          pushError("chat.attachment.tooLarge");
          continue;
        }
        if (totalBase64Chars.value + base64.length > MAX_TOTAL_BASE64_CHARS) {
          pushError("chat.attachment.totalTooLarge");
          continue;
        }
        items.value = [
          ...items.value,
          {
            id: createId(),
            name: file.name || "image",
            mime: mimeFromDataUrl(dataUrl) || file.type,
            size: file.size,
            base64,
            dataUrl,
          },
        ];
      }
    } finally {
      busy.value = false;
    }
  }

  return {
    items,
    errors,
    busy,
    blocks,
    hasAttachments,
    totalBase64Chars,
    addFiles,
    remove,
    clear,
    clearErrors,
  };
}
