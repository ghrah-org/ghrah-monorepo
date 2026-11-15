// SPDX-FileCopyrightText: 2026 chenxya <chenxya@ghrah.org>
//
// SPDX-License-Identifier: Apache-2.0

import type { ChatBlock } from "@ghrah/observer-core";
import { uploadAttachment, type UploadedAttachment } from "@ghrah/observer-core";
import { computed, ref } from "vue";

/** M1/M2 限额（与 Subject 校验同源；配置切片落地前先用常量）。 */
export const MAX_IMAGE_BLOCKS = 4;
export const MAX_TOTAL_BLOCKS = 8;
export const MAX_VIDEO_BLOCKS = 1;
/** 单块 base64 字符串上限（4 MiB）。 */
export const MAX_IMAGE_BASE64_CHARS = 4 * 1024 * 1024;
/** 单消息内联 base64 总量上限（8 MiB）。 */
export const MAX_TOTAL_BASE64_CHARS = 8 * 1024 * 1024;
/**
 * 小文件内联的字节上限：2.8 MiB（base64 约 3.7 MiB 字符）。
 * Subject 按「内联载荷字符数」校验单块 ≤4 MiB，这里留出余量避免正好卡线。
 */
export const MAX_FILE_BYTES = Math.floor(2.8 * 1024 * 1024);
/** 单块内联 base64 字符上限（与 Subject 单块校验同源）。 */
export const MAX_FILE_BASE64_CHARS = 4 * 1024 * 1024;
/** 视频原文件上传上限。 */
export const MAX_VIDEO_BYTES = 64 * 1024 * 1024;
export const MAX_VIDEO_FRAMES = 4;
export const MAX_FRAME_BASE64_CHARS = 1.5 * 1024 * 1024;
export const MAX_FRAMES_TOTAL_BASE64_CHARS = 4 * 1024 * 1024;
/** 抽帧长边上限。 */
export const MAX_FRAME_EDGE = 1280;

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
  | "chat.attachment.failed"
  | "chat.attachment.fileTooLarge"
  | "chat.attachment.videoTooLarge"
  | "chat.attachment.videoDecodeFailed"
  | "chat.attachment.frameExtractFailed"
  | "chat.attachment.uploadFailed"
  | "chat.attachment.uploadTimeout";

export interface AttachmentBase {
  id: string;
  /** 原始文件名（展示用）。 */
  name: string;
  /** 实际发送的 MIME。 */
  mime: string;
  /** 原始文件字节数（展示用）。 */
  size: number;
}

export interface ImageAttachment extends AttachmentBase {
  kind: "image";
  /** 压缩后的 base64（无 data: 前缀）。 */
  base64: string;
  /** 同样内容的 data URL，直接用作缩略图。 */
  dataUrl: string;
}

export interface FileAttachment extends AttachmentBase {
  kind: "file";
  /** 内联文件字节的 base64（无 data: 前缀）。 */
  base64: string;
}

export interface VideoFrame {
  type: "image";
  base64: string;
  mime_type: string;
}

export type VideoAttachmentStatus = "uploading" | "ready" | "error";

export interface VideoAttachment extends AttachmentBase {
  kind: "video";
  /** 上传成功后的附件 id（sha256）。 */
  attachmentId: string;
  durationS: number;
  width: number;
  height: number;
  frames: VideoFrame[];
  /** 第一帧 data URL，作 chip 封面/无附件 URL 时的回退。 */
  coverDataUrl: string;
  status: VideoAttachmentStatus;
  /** 0..1；上传中才有意义。 */
  progress: number;
  errorKey?: AttachmentErrorKey;
  /** 重试所需的原始文件（仅 UI 态，不发送）。 */
  file?: File;
}

export type PendingAttachment = ImageAttachment | FileAttachment | VideoAttachment;

export interface VideoMetadata {
  durationS: number;
  width: number;
  height: number;
  frames: VideoFrame[];
}

export interface AttachmentDeps {
  /** 图片压缩为 data URL；默认 FileReader + canvas。 */
  compress?: (file: File, maxChars: number) => Promise<string>;
  /** 文件读为 data URL；默认 FileReader。 */
  readAsDataUrl?: (file: File) => Promise<string>;
  /** 视频元数据 + 抽帧；默认 <video> + canvas。 */
  probeVideo?: (file: File, options: VideoProbeOptions) => Promise<VideoMetadata>;
  /** 上传原始字节；默认走 observer-core 的 XHR 客户端。 */
  upload?: (file: File, options: UploadOptions) => Promise<UploadedAttachment>;
  createId?: () => string;
}

export interface VideoProbeOptions {
  maxFrames: number;
  maxEdge: number;
  maxFrameChars: number;
  maxTotalFrameChars: number;
  signal?: AbortSignal;
}

export interface UploadOptions {
  projectId: string;
  onProgress: (loaded: number, total: number) => void;
  signal?: AbortSignal;
}

export interface UseAttachmentsOptions {
  /** 当前项目（惰性读取，项目切换后仍取最新值）。 */
  projectId?: string | (() => string | undefined);
  deps?: AttachmentDeps;
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

/** 附件处理的可映射错误；键即 i18n key（也用于测试注入）。 */
export class AttachmentError extends Error {
  constructor(readonly key: AttachmentErrorKey) {
    super(key);
    this.name = "AttachmentError";
  }
}

async function readFileAsDataUrl(file: File): Promise<string> {
  return new Promise<string>((resolve, reject) => {
    const reader = new FileReader();
    reader.onerror = () => reject(new AttachmentError("chat.attachment.failed"));
    reader.onload = () => {
      const result = reader.result;
      if (typeof result === "string") resolve(result);
      else reject(new AttachmentError("chat.attachment.failed"));
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
    image.onerror = () => reject(new AttachmentError("chat.attachment.failed"));
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
  if (file.type === "image/gif") throw new AttachmentError("chat.attachment.tooLarge");
  const decoded = await decodeImage(original);
  try {
    let scale = Math.min(1, 2048 / Math.max(decoded.width, decoded.height));
    for (let attempt = 0; attempt < 8; attempt += 1) {
      const canvas = document.createElement("canvas");
      canvas.width = Math.max(1, Math.round(decoded.width * scale));
      canvas.height = Math.max(1, Math.round(decoded.height * scale));
      const context = canvas.getContext("2d");
      if (!context) throw new AttachmentError("chat.attachment.failed");
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
  throw new AttachmentError("chat.attachment.tooLarge");
}

function waitFor(video: HTMLVideoElement, event: string, errorKey: AttachmentErrorKey) {
  return new Promise<void>((resolve, reject) => {
    const timer = setTimeout(
      () => reject(new AttachmentError("chat.attachment.videoDecodeFailed")),
      15_000,
    );
    const done = () => {
      cleanup();
      resolve();
    };
    const fail = () => {
      cleanup();
      reject(new AttachmentError(errorKey));
    };
    const cleanup = () => {
      clearTimeout(timer);
      video.removeEventListener(event, done);
      video.removeEventListener("error", fail);
    };
    video.addEventListener(event, done, { once: true });
    video.addEventListener("error", fail, { once: true });
  });
}

async function defaultProbeVideo(file: File, options: VideoProbeOptions): Promise<VideoMetadata> {
  const objectUrl = URL.createObjectURL(file);
  try {
    const video = document.createElement("video");
    video.preload = "auto";
    video.muted = true;
    video.playsInline = true;
    video.src = objectUrl;
    await waitFor(video, "loadedmetadata", "chat.attachment.videoDecodeFailed");
    const duration = Number.isFinite(video.duration) ? video.duration : 0;
    if (!(duration > 0) || !video.videoWidth || !video.videoHeight) {
      throw new AttachmentError("chat.attachment.videoDecodeFailed");
    }
    const frames: VideoFrame[] = [];
    let totalChars = 0;
    const count = Math.min(MAX_VIDEO_FRAMES, options.maxFrames);
    for (let index = 0; index < count; index += 1) {
      if (options.signal?.aborted) throw new AttachmentError("chat.attachment.failed");
      // 均分取帧，避开首尾最容易黑屏的位置。
      const target = duration * ((index + 0.5) / count);
      video.currentTime = Math.min(Math.max(target, 0), Math.max(duration - 0.05, 0));
      await waitFor(video, "seeked", "chat.attachment.frameExtractFailed");
      const scale = Math.min(1, options.maxEdge / Math.max(video.videoWidth, video.videoHeight));
      const canvas = document.createElement("canvas");
      canvas.width = Math.max(1, Math.round(video.videoWidth * scale));
      canvas.height = Math.max(1, Math.round(video.videoHeight * scale));
      const context = canvas.getContext("2d");
      if (!context) throw new AttachmentError("chat.attachment.frameExtractFailed");
      context.drawImage(video, 0, 0, canvas.width, canvas.height);
      let encoded = canvas.toDataURL("image/jpeg", 0.72);
      if (base64FromDataUrl(encoded).length > options.maxFrameChars) {
        encoded = canvas.toDataURL("image/jpeg", 0.45);
      }
      const base64 = base64FromDataUrl(encoded);
      if (!base64 || base64.length > options.maxFrameChars) {
        throw new AttachmentError("chat.attachment.frameExtractFailed");
      }
      totalChars += base64.length;
      frames.push({ type: "image", base64, mime_type: "image/jpeg" });
    }
    if (totalChars > options.maxTotalFrameChars) {
      throw new AttachmentError("chat.attachment.frameExtractFailed");
    }
    return { durationS: duration, width: video.videoWidth, height: video.videoHeight, frames };
  } finally {
    URL.revokeObjectURL(objectUrl);
  }
}

function errorKey(error: unknown): AttachmentErrorKey {
  if (error instanceof AttachmentError) return error.key;
  return "chat.attachment.failed";
}

function uploadErrorKey(error: unknown): AttachmentErrorKey {
  const code =
    typeof error === "object" && error !== null && "code" in error
      ? String((error as { code?: unknown }).code ?? "")
      : "";
  if (code === "timeout") return "chat.attachment.uploadTimeout";
  if (code === "attachment_too_large") return "chat.attachment.videoTooLarge";
  if (code === "unsupported_attachment_type") return "chat.attachment.unsupported";
  return "chat.attachment.uploadFailed";
}

function inlineCharsOf(item: PendingAttachment): number {
  if (item.kind === "image" || item.kind === "file") return item.base64.length;
  return item.frames.reduce((sum, frame) => sum + frame.base64.length, 0);
}

function isImageFile(file: File): boolean {
  return file.type.startsWith("image/") && isSupportedImageType(file.type);
}

function isVideoFile(file: File): boolean {
  return file.type.startsWith("video/");
}

/**
 * composer 附件状态：选择/粘贴/拖拽三入口统一走 addFiles。
 * - 图片：内联压缩（M1 语义不变）；
 * - 小文件：内联 base64 file 块（≤4 MiB）；
 * - 视频：浏览器抽帧 + 原文件上传附件，消息里发 video 块（attachment_id + frames）。
 */
export function useAttachments(options: UseAttachmentsOptions = {}) {
  const deps = options.deps ?? {};
  const items = ref<PendingAttachment[]>([]);
  const errors = ref<AttachmentErrorKey[]>([]);
  const processing = ref(0);
  const compress = deps.compress ?? defaultCompressImage;
  const readAsDataUrl = deps.readAsDataUrl ?? readFileAsDataUrl;
  const probeVideo = deps.probeVideo ?? defaultProbeVideo;
  const resolveProjectId = () =>
    typeof options.projectId === "function" ? options.projectId() : options.projectId;
  const upload =
    deps.upload ??
    ((file: File, uploadOptions: UploadOptions) =>
      uploadAttachment(uploadOptions.projectId, file, {
        mimeType: file.type || "application/octet-stream",
        filename: file.name || null,
        onProgress: (progress) => uploadOptions.onProgress(progress.loaded, progress.total),
        signal: uploadOptions.signal,
      }));
  const createId =
    deps.createId ??
    (() => `att-${Date.now().toString(36)}-${Math.random().toString(36).slice(2, 8)}`);
  const controllers = new Map<string, AbortController>();

  const busy = computed(() => processing.value > 0);

  const inlineBase64Chars = computed(() =>
    items.value.reduce((sum, item) => sum + inlineCharsOf(item), 0),
  );

  const blocks = computed<ChatBlock[]>(() => {
    const out: ChatBlock[] = [];
    for (const item of items.value) {
      if (item.kind === "image") {
        out.push({ type: "image", base64: item.base64, mime_type: item.mime });
      } else if (item.kind === "file") {
        out.push({
          type: "file",
          base64: item.base64,
          mime_type: item.mime,
          filename: item.name,
        });
      } else if (item.kind === "video" && item.status === "ready" && item.attachmentId) {
        out.push({
          type: "video",
          attachment_id: item.attachmentId,
          mime_type: item.mime,
          filename: item.name,
          duration_s: item.durationS,
          width: item.width,
          height: item.height,
          frames: item.frames.map((frame) => ({
            type: "image" as const,
            base64: frame.base64,
            mime_type: frame.mime_type,
          })),
        });
      }
    }
    return out;
  });
  const hasAttachments = computed(() => items.value.length > 0);
  const hasReadyBlocks = computed(() => blocks.value.length > 0);

  function pushError(key: AttachmentErrorKey) {
    if (errors.value.includes(key)) return;
    errors.value = [...errors.value, key];
  }

  function clearErrors() {
    if (errors.value.length > 0) errors.value = [];
  }

  function patch(id: string, next: (item: PendingAttachment) => PendingAttachment) {
    items.value = items.value.map((item) => (item.id === id ? next(item) : item));
  }

  function remove(id: string) {
    controllers.get(id)?.abort();
    controllers.delete(id);
    items.value = items.value.filter((item) => item.id !== id);
  }

  function clear() {
    for (const controller of controllers.values()) controller.abort();
    controllers.clear();
    items.value = [];
    errors.value = [];
  }

  function countKind(kind: PendingAttachment["kind"]): number {
    return items.value.filter((item) => item.kind === kind).length;
  }

  function canAddBlock(): boolean {
    return items.value.length < MAX_TOTAL_BLOCKS;
  }

  async function addImage(file: File): Promise<void> {
    if (countKind("image") >= MAX_IMAGE_BLOCKS || !canAddBlock()) {
      pushError("chat.attachment.totalTooLarge");
      return;
    }
    const dataUrl = await compress(file, MAX_IMAGE_BASE64_CHARS);
    const base64 = base64FromDataUrl(dataUrl);
    if (!base64) throw new AttachmentError("chat.attachment.failed");
    if (base64.length > MAX_IMAGE_BASE64_CHARS) {
      throw new AttachmentError("chat.attachment.tooLarge");
    }
    if (inlineBase64Chars.value + base64.length > MAX_TOTAL_BASE64_CHARS) {
      throw new AttachmentError("chat.attachment.totalTooLarge");
    }
    items.value = [
      ...items.value,
      {
        id: createId(),
        kind: "image",
        name: file.name || "image",
        mime: mimeFromDataUrl(dataUrl) || file.type,
        size: file.size,
        base64,
        dataUrl,
      },
    ];
  }

  async function addFileBlock(file: File): Promise<void> {
    if (!canAddBlock()) {
      pushError("chat.attachment.totalTooLarge");
      return;
    }
    if (file.size > MAX_FILE_BYTES) {
      pushError("chat.attachment.fileTooLarge");
      return;
    }
    const dataUrl = await readAsDataUrl(file);
    const base64 = base64FromDataUrl(dataUrl);
    if (!base64) throw new AttachmentError("chat.attachment.failed");
    // 双约束：字节上限之外，Subject 还按 base64 字符数校验单块 ≤4 MiB。
    if (base64.length > MAX_FILE_BASE64_CHARS) {
      throw new AttachmentError("chat.attachment.fileTooLarge");
    }
    if (inlineBase64Chars.value + base64.length > MAX_TOTAL_BASE64_CHARS) {
      throw new AttachmentError("chat.attachment.totalTooLarge");
    }
    items.value = [
      ...items.value,
      {
        id: createId(),
        kind: "file",
        name: file.name || "file",
        mime: file.type || "application/octet-stream",
        size: file.size,
        base64,
      },
    ];
  }

  async function processVideo(id: string, file: File): Promise<void> {
    const controller = new AbortController();
    controllers.set(id, controller);
    processing.value += 1;
    try {
      const metadata = await probeVideo(file, {
        maxFrames: MAX_VIDEO_FRAMES,
        maxEdge: MAX_FRAME_EDGE,
        maxFrameChars: MAX_FRAME_BASE64_CHARS,
        maxTotalFrameChars: MAX_FRAMES_TOTAL_BASE64_CHARS,
        signal: controller.signal,
      });
      const frameChars = metadata.frames.reduce((sum, frame) => sum + frame.base64.length, 0);
      const otherChars = items.value
        .filter((item) => item.id !== id)
        .reduce((sum, item) => sum + inlineCharsOf(item), 0);
      if (otherChars + frameChars > MAX_TOTAL_BASE64_CHARS) {
        throw new AttachmentError("chat.attachment.totalTooLarge");
      }
      const cover = metadata.frames[0];
      patch(id, (item) =>
        item.kind === "video"
          ? {
              ...item,
              durationS: metadata.durationS,
              width: metadata.width,
              height: metadata.height,
              frames: metadata.frames,
              coverDataUrl: cover ? `data:${cover.mime_type};base64,${cover.base64}` : "",
              status: "uploading",
              progress: 0,
            }
          : item,
      );
      const projectId = resolveProjectId();
      if (!projectId) throw new AttachmentError("chat.attachment.uploadFailed");
      const uploaded = await upload(file, {
        projectId,
        signal: controller.signal,
        onProgress: (loaded, total) =>
          patch(id, (item) =>
            item.kind === "video"
              ? { ...item, progress: total > 0 ? Math.min(1, loaded / total) : 0 }
              : item,
          ),
      });
      patch(id, (item) =>
        item.kind === "video"
          ? {
              ...item,
              attachmentId: uploaded.attachmentId,
              size: uploaded.size || item.size,
              status: "ready",
              progress: 1,
              errorKey: undefined,
            }
          : item,
      );
    } catch (error) {
      patch(id, (item) =>
        item.kind === "video"
          ? {
              ...item,
              status: "error",
              errorKey: error instanceof AttachmentError ? error.key : uploadErrorKey(error),
            }
          : item,
      );
    } finally {
      controllers.delete(id);
      processing.value = Math.max(0, processing.value - 1);
    }
  }

  async function addVideo(file: File): Promise<void> {
    if (countKind("video") >= MAX_VIDEO_BLOCKS) {
      pushError("chat.attachment.totalTooLarge");
      return;
    }
    if (!canAddBlock()) {
      pushError("chat.attachment.totalTooLarge");
      return;
    }
    if (file.size > MAX_VIDEO_BYTES) {
      pushError("chat.attachment.videoTooLarge");
      return;
    }
    const id = createId();
    items.value = [
      ...items.value,
      {
        id,
        kind: "video",
        name: file.name || "video",
        mime: file.type || "video/mp4",
        size: file.size,
        attachmentId: "",
        durationS: 0,
        width: 0,
        height: 0,
        frames: [],
        coverDataUrl: "",
        status: "uploading",
        progress: 0,
        file,
      },
    ];
    await processVideo(id, file);
  }

  async function addFiles(files: Iterable<File>) {
    const incoming = Array.from(files);
    if (incoming.length === 0) return;
    for (const file of incoming) {
      try {
        if (isImageFile(file)) {
          await addImage(file);
        } else if (file.type.startsWith("image/")) {
          // 图片但不在白名单（如 svg）：明确拒绝，不静默当文件。
          pushError("chat.attachment.unsupported");
        } else if (isVideoFile(file)) {
          await addVideo(file);
        } else {
          await addFileBlock(file);
        }
      } catch (error) {
        pushError(errorKey(error));
      }
    }
  }

  async function retry(id: string) {
    const item = items.value.find((candidate) => candidate.id === id);
    if (!item || item.kind !== "video" || !item.file || processing.value > 0) return;
    patch(id, (current) =>
      current.kind === "video"
        ? { ...current, status: "uploading", progress: 0, errorKey: undefined }
        : current,
    );
    await processVideo(id, item.file);
  }

  return {
    items,
    errors,
    busy,
    blocks,
    hasAttachments,
    hasReadyBlocks,
    inlineBase64Chars,
    addFiles,
    remove,
    retry,
    clear,
    clearErrors,
  };
}
