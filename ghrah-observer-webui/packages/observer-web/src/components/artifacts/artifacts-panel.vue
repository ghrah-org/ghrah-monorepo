<!--
SPDX-FileCopyrightText: 2026 chenxya <chenxya@ghrah.org>

SPDX-License-Identifier: Apache-2.0
-->

<script setup lang="ts">
import type { ArtifactView, WorkspaceEntryView } from "@ghrah/observer-core";
import {
  artifactContentUrl,
  HttpError,
  listArtifacts,
  listWorkspaceFiles,
  publishArtifact,
  readArtifact,
  readWorkspaceFile,
  useProjectsStore,
  workspaceFileContentUrl,
} from "@ghrah/observer-core";
import { computed, onBeforeUnmount, ref, watch } from "vue";
import { useI18n } from "vue-i18n";
import UiButton from "@/components/ui/button.vue";
import UiSelect from "@/components/ui/select.vue";
import UiTextField from "@/components/ui/text-field.vue";

const props = defineProps<{ projectId: string }>();
const { t } = useI18n();
const projects = useProjectsStore();

type PreviewKind = "none" | "image" | "text" | "pdf" | "binary";
type PreviewTarget =
  { type: "file"; entry: WorkspaceEntryView } | { type: "artifact"; artifact: ArtifactView };

const TEXT_EXTENSIONS = new Set([
  "txt",
  "md",
  "markdown",
  "json",
  "xml",
  "yaml",
  "yml",
  "csv",
  "log",
  "py",
  "ts",
  "js",
  "css",
  "html",
  "htm",
  "sh",
  "toml",
  "ini",
  "svg",
]);
const MIME_BY_EXTENSION: Readonly<Record<string, string>> = {
  svg: "image/svg+xml",
  png: "image/png",
  jpg: "image/jpeg",
  jpeg: "image/jpeg",
  webp: "image/webp",
  gif: "image/gif",
  pdf: "application/pdf",
  json: "application/json",
  txt: "text/plain",
  md: "text/markdown",
  csv: "text/csv",
  dxf: "application/dxf",
};
const PREVIEW_TEXT_LIMIT = 200 * 1024;

const workspaces = computed(() => {
  const project = projects.projects.get(props.projectId);
  const mounted = project?.workspaces ?? [];
  if (mounted.length > 0) return mounted;
  // 兼容：Project 未挂载 workspaces 时，从 Agent 的 path_grants 反推可用工作区 id。
  const ids = new Set<string>();
  for (const agent of project?.agents ?? []) {
    for (const grant of agent.path_grants ?? []) {
      if (grant.workspace_id) ids.add(grant.workspace_id);
    }
  }
  return [...ids].map((workspaceId) => ({
    workspace_id: workspaceId,
    role: null,
    default_for_agents: false,
  }));
});
const workspaceId = ref("");
const path = ref("");
const entries = ref<WorkspaceEntryView[]>([]);
const listingLoading = ref(false);
const listingError = ref("");
const selected = ref<WorkspaceEntryView | null>(null);
const preview = ref<{
  kind: PreviewKind;
  loading: boolean;
  error: string;
  url: string;
  text: string;
  truncated: boolean;
}>({ kind: "none", loading: false, error: "", url: "", text: "", truncated: false });
const previewTarget = ref<PreviewTarget | null>(null);
const title = ref("");
const publishing = ref(false);
const publishError = ref("");
const publishNotice = ref("");
const artifacts = ref<ArtifactView[]>([]);
const artifactsLoading = ref(false);
const artifactsError = ref("");
let objectUrl = "";

const workspaceOptions = computed(() =>
  workspaces.value.map((workspace) => ({
    id: workspace.workspace_id,
    label: workspace.role
      ? `${workspace.workspace_id} · ${workspace.role}`
      : workspace.workspace_id,
  })),
);

const breadcrumbs = computed(() => {
  const segments = path.value.split("/").filter(Boolean);
  const items = [{ label: t("artifacts.root"), path: "" }];
  let current = "";
  for (const segment of segments) {
    current = current ? `${current}/${segment}` : segment;
    items.push({ label: segment, path: current });
  }
  return items;
});

const selectedIsArtifact = computed(() => previewTarget.value?.type === "artifact");
const selectedName = computed(() =>
  previewTarget.value?.type === "artifact"
    ? previewTarget.value.artifact.filename
    : (selected.value?.name ?? ""),
);
const selectedSize = computed(() =>
  previewTarget.value?.type === "artifact"
    ? previewTarget.value.artifact.size
    : (selected.value?.size ?? null),
);
const selectedMime = computed(() =>
  previewTarget.value?.type === "artifact"
    ? previewTarget.value.artifact.mime_type
    : selected.value
      ? guessMime(selected.value.name)
      : null,
);
const canPublish = computed(
  () => selected.value !== null && !publishing.value && workspaceId.value !== "",
);

function errorKey(error: unknown): string {
  const code = error instanceof HttpError ? error.code : "unavailable";
  const map: Record<string, string> = {
    workspace_not_found: "artifacts.errors.workspaceNotFound",
    path_outside_workspace: "artifacts.errors.pathOutsideWorkspace",
    artifact_not_found: "artifacts.errors.artifactNotFound",
    artifact_too_large: "artifacts.errors.artifactTooLarge",
    unsupported_artifact_type: "artifacts.errors.unsupportedArtifactType",
    access_denied: "artifacts.errors.accessDenied",
    invalid_request: "artifacts.errors.invalidRequest",
    unavailable: "artifacts.errors.unavailable",
  };
  return map[code] ?? "artifacts.errors.unavailable";
}

function extensionOf(name: string): string {
  const match = /\.([A-Za-z0-9]+)$/.exec(name);
  return (match?.[1] ?? "").toLowerCase();
}

function guessMime(name: string): string | null {
  return MIME_BY_EXTENSION[extensionOf(name)] ?? null;
}

function previewKind(mime: string, name: string): PreviewKind {
  if (mime.startsWith("image/")) return "image";
  if (mime === "application/pdf" || extensionOf(name) === "pdf") return "pdf";
  if (mime.startsWith("text/") || TEXT_EXTENSIONS.has(extensionOf(name))) return "text";
  return "binary";
}

function formatSize(bytes: number | null | undefined): string {
  if (bytes == null) return "—";
  if (bytes >= 1024 * 1024) return `${(bytes / (1024 * 1024)).toFixed(1)} MB`;
  if (bytes >= 1024) return `${Math.round(bytes / 1024)} KB`;
  return `${bytes} B`;
}

function formatTime(value: string | null | undefined): string {
  if (!value) return "—";
  const date = new Date(value);
  return Number.isNaN(date.getTime()) ? value : date.toLocaleString();
}

function clearObjectUrl() {
  if (objectUrl && typeof URL.revokeObjectURL === "function") URL.revokeObjectURL(objectUrl);
  objectUrl = "";
}

function resetPreview() {
  clearObjectUrl();
  previewTarget.value = null;
  preview.value = { kind: "none", loading: false, error: "", url: "", text: "", truncated: false };
}

async function loadListing(targetPath = path.value) {
  if (!workspaceId.value) {
    entries.value = [];
    return;
  }
  listingLoading.value = true;
  listingError.value = "";
  try {
    const listing = await listWorkspaceFiles(props.projectId, workspaceId.value, targetPath);
    path.value = listing.path;
    entries.value = listing.entries;
    selected.value = null;
  } catch (error) {
    entries.value = [];
    listingError.value = t(errorKey(error));
  } finally {
    listingLoading.value = false;
  }
}

async function loadArtifactList() {
  artifactsLoading.value = true;
  artifactsError.value = "";
  try {
    artifacts.value = await listArtifacts(props.projectId);
  } catch (error) {
    artifacts.value = [];
    artifactsError.value = t(errorKey(error));
  } finally {
    artifactsLoading.value = false;
  }
}

async function showPreview(target: PreviewTarget) {
  resetPreview();
  previewTarget.value = target;
  preview.value.loading = true;
  try {
    const bytes =
      target.type === "file"
        ? await readWorkspaceFile(props.projectId, workspaceId.value, target.entry.path)
        : await readArtifact(props.projectId, target.artifact.artifact_id);
    const name = target.type === "file" ? target.entry.name : target.artifact.filename;
    const kind = previewKind(bytes.mimeType, name);
    if (kind === "image" || kind === "pdf") {
      const copy = new Uint8Array(bytes.bytes);
      const blob = new Blob([copy.buffer], { type: bytes.mimeType });
      objectUrl =
        typeof URL.createObjectURL === "function"
          ? URL.createObjectURL(blob)
          : target.type === "file"
            ? workspaceFileContentUrl(props.projectId, workspaceId.value, target.entry.path)
            : artifactContentUrl(props.projectId, target.artifact.artifact_id);
      preview.value = {
        kind,
        loading: false,
        error: "",
        url: objectUrl,
        text: "",
        truncated: false,
      };
      return;
    }
    if (kind === "text") {
      const decoded = new TextDecoder("utf-8", { fatal: false }).decode(bytes.bytes);
      preview.value = {
        kind,
        loading: false,
        error: "",
        url: "",
        text: decoded.slice(0, PREVIEW_TEXT_LIMIT),
        truncated: decoded.length > PREVIEW_TEXT_LIMIT,
      };
      return;
    }
    preview.value = {
      kind: "binary",
      loading: false,
      error: "",
      url: "",
      text: "",
      truncated: false,
    };
  } catch (error) {
    preview.value = {
      kind: "none",
      loading: false,
      error: t(errorKey(error)),
      url: "",
      text: "",
      truncated: false,
    };
  }
}

function goTo(targetPath: string) {
  path.value = targetPath;
  selected.value = null;
  resetPreview();
  void loadListing(targetPath);
}

function openEntry(entry: WorkspaceEntryView) {
  if (entry.type === "dir") {
    selected.value = null;
    resetPreview();
    void loadListing(entry.path);
    return;
  }
  selected.value = entry;
  void showPreview({ type: "file", entry });
}

function openArtifact(artifact: ArtifactView) {
  selected.value = null;
  void showPreview({ type: "artifact", artifact });
}

async function publishSelected() {
  if (!selected.value || !canPublish.value) return;
  publishing.value = true;
  publishError.value = "";
  publishNotice.value = "";
  try {
    const view = await publishArtifact(props.projectId, {
      workspace_id: workspaceId.value,
      path: selected.value.path,
      mime_type: guessMime(selected.value.name),
      title: title.value.trim() || null,
    });
    publishNotice.value = t("artifacts.publish.success", {
      name: view.title || view.filename,
    });
    title.value = "";
    await loadArtifactList();
  } catch (error) {
    publishError.value = t(errorKey(error));
  } finally {
    publishing.value = false;
  }
}

function refreshAll() {
  void loadListing();
  void loadArtifactList();
}

function onWorkspaceChange(next: string) {
  workspaceId.value = next || workspaceOptions.value[0]?.id || "";
  path.value = "";
  selected.value = null;
  resetPreview();
  void loadListing("");
}

watch(
  () => [props.projectId, workspaceOptions.value.map((item) => item.id).join("|")] as const,
  () => {
    const available = workspaceOptions.value;
    if (!available.some((item) => item.id === workspaceId.value)) {
      workspaceId.value = available[0]?.id ?? "";
    }
    path.value = "";
    selected.value = null;
    resetPreview();
    if (workspaceId.value) void loadListing("");
    else entries.value = [];
    void loadArtifactList();
  },
  { immediate: true },
);

onBeforeUnmount(clearObjectUrl);
</script>

<template>
  <section class="artifacts-panel" :aria-label="t('artifacts.title')">
    <header class="artifacts-head">
      <div>
        <h2>{{ t("artifacts.title") }}</h2>
        <p class="artifacts-note">{{ t("artifacts.subtitle") }}</p>
      </div>
      <div class="artifacts-actions">
        <label v-if="workspaceOptions.length > 0" class="artifacts-field">
          <span>{{ t("artifacts.workspace") }}</span>
          <UiSelect :model-value="workspaceId" @update:model-value="onWorkspaceChange">
            <option v-for="option in workspaceOptions" :key="option.id" :value="option.id">
              {{ option.label }}
            </option>
          </UiSelect>
        </label>
        <UiButton type="button" variant="secondary" @click="refreshAll">
          {{ t("artifacts.refresh") }}
        </UiButton>
      </div>
    </header>

    <p v-if="workspaceOptions.length === 0" class="artifacts-state artifacts-state-empty">
      {{ t("artifacts.empty.noWorkspaces") }}
    </p>

    <div v-else class="artifacts-grid">
      <section class="artifacts-column" :aria-label="t('artifacts.browser.title')">
        <header class="artifacts-column-head">
          <h3>{{ t("artifacts.browser.title") }}</h3>
        </header>
        <nav class="artifacts-breadcrumb" :aria-label="t('artifacts.browser.breadcrumb')">
          <button
            v-for="(crumb, index) in breadcrumbs"
            :key="crumb.path || 'root'"
            type="button"
            class="artifacts-crumb"
            :aria-current="index === breadcrumbs.length - 1 ? 'location' : undefined"
            @click="goTo(crumb.path)"
          >
            {{ crumb.label }}
          </button>
        </nav>
        <p v-if="listingLoading" class="artifacts-note">{{ t("artifacts.loading") }}</p>
        <p v-else-if="listingError" class="artifacts-error" role="alert">{{ listingError }}</p>
        <p v-else-if="entries.length === 0" class="artifacts-note">
          {{ t("artifacts.empty.dir") }}
        </p>
        <ul v-else class="artifacts-entries">
          <li v-for="entry in entries" :key="entry.path">
            <button
              type="button"
              class="artifacts-entry"
              :class="{ 'artifacts-entry-selected': selected?.path === entry.path }"
              @click="openEntry(entry)"
            >
              <span class="artifacts-entry-icon" aria-hidden="true">{{
                entry.type === "dir" ? "▸" : "·"
              }}</span>
              <span class="artifacts-entry-name">{{ entry.name }}</span>
              <span v-if="entry.type === 'file'" class="artifacts-entry-size">
                {{ formatSize(entry.size) }}
              </span>
            </button>
          </li>
        </ul>
      </section>

      <section class="artifacts-column" :aria-label="t('artifacts.preview.title')">
        <header class="artifacts-column-head">
          <h3>{{ t("artifacts.preview.title") }}</h3>
          <span v-if="selectedIsArtifact" class="artifacts-badge">
            {{ t("artifacts.preview.artifactBadge") }}
          </span>
        </header>
        <p v-if="previewTarget === null" class="artifacts-note">
          {{ t("artifacts.preview.none") }}
        </p>
        <template v-else>
          <dl class="artifacts-facts">
            <div>
              <dt>{{ t("artifacts.preview.name") }}</dt>
              <dd>{{ selectedName }}</dd>
            </div>
            <div>
              <dt>{{ t("artifacts.preview.size") }}</dt>
              <dd>{{ formatSize(selectedSize) }}</dd>
            </div>
            <div v-if="selectedMime">
              <dt>{{ t("artifacts.preview.mime") }}</dt>
              <dd>{{ selectedMime }}</dd>
            </div>
          </dl>
          <p v-if="preview.loading" class="artifacts-note">{{ t("artifacts.preview.loading") }}</p>
          <p v-else-if="preview.error" class="artifacts-error" role="alert">{{ preview.error }}</p>
          <template v-else-if="preview.kind === 'image'">
            <img class="artifacts-preview-media" :src="preview.url" :alt="selectedName" />
          </template>
          <template v-else-if="preview.kind === 'pdf'">
            <iframe
              class="artifacts-preview-frame"
              :src="preview.url"
              :title="t('artifacts.preview.pdf')"
            />
          </template>
          <template v-else-if="preview.kind === 'text'">
            <pre class="artifacts-preview-text">{{ preview.text }}</pre>
            <p v-if="preview.truncated" class="artifacts-note">
              {{ t("artifacts.preview.truncated") }}
            </p>
          </template>
          <p v-else-if="preview.kind === 'binary'" class="artifacts-note">
            {{ t("artifacts.preview.binary") }}
          </p>

          <template v-if="!selectedIsArtifact">
            <div class="artifacts-publish">
              <label class="artifacts-field">
                <span>{{ t("artifacts.publish.titleLabel") }}</span>
                <UiTextField
                  v-model="title"
                  :placeholder="t('artifacts.publish.titlePlaceholder')"
                />
              </label>
              <UiButton
                type="button"
                variant="primary"
                :disabled="!canPublish"
                @click="publishSelected"
              >
                {{ publishing ? t("artifacts.publish.publishing") : t("artifacts.publish.action") }}
              </UiButton>
            </div>
            <p v-if="publishNotice" class="artifacts-notice" role="status">{{ publishNotice }}</p>
            <p v-if="publishError" class="artifacts-error" role="alert">{{ publishError }}</p>
          </template>
        </template>
      </section>

      <section class="artifacts-column" :aria-label="t('artifacts.list.title')">
        <header class="artifacts-column-head">
          <h3>{{ t("artifacts.list.title") }}</h3>
          <span class="artifacts-note">{{
            t("artifacts.list.count", { count: artifacts.length })
          }}</span>
        </header>
        <p v-if="artifactsLoading" class="artifacts-note">{{ t("artifacts.loading") }}</p>
        <p v-else-if="artifactsError" class="artifacts-error" role="alert">{{ artifactsError }}</p>
        <p v-else-if="artifacts.length === 0" class="artifacts-note">
          {{ t("artifacts.empty.artifacts") }}
        </p>
        <ul v-else class="artifacts-list">
          <li v-for="artifact in artifacts" :key="artifact.artifact_id" class="artifacts-item">
            <div>
              <strong class="artifacts-item-name">{{ artifact.title || artifact.filename }}</strong>
              <p class="artifacts-item-meta">
                {{ artifact.mime_type }} · {{ formatSize(artifact.size) }} ·
                {{ formatTime(artifact.published_at) }}
              </p>
              <p class="artifacts-item-source">
                {{
                  t("artifacts.list.source", {
                    path: `${artifact.source.workspace_id}:${artifact.source.path}`,
                  })
                }}
              </p>
            </div>
            <div class="artifacts-item-actions">
              <UiButton type="button" variant="secondary" size="sm" @click="openArtifact(artifact)">
                {{ t("artifacts.list.preview") }}
              </UiButton>
              <a
                class="artifacts-link"
                :href="artifactContentUrl(projectId, artifact.artifact_id)"
                target="_blank"
                rel="noreferrer"
              >
                {{ t("artifacts.list.open") }}
              </a>
              <a
                class="artifacts-link"
                :href="artifactContentUrl(projectId, artifact.artifact_id)"
                :download="artifact.filename"
              >
                {{ t("artifacts.list.download") }}
              </a>
            </div>
          </li>
        </ul>
      </section>
    </div>
  </section>
</template>

<style scoped>
.artifacts-panel {
  display: flex;
  min-width: 0;
  flex-direction: column;
  gap: 0.75rem;
  padding: 1rem;
  color: var(--color-text);
  font-size: var(--font-ui-body);
  overflow-wrap: anywhere;
}

.artifacts-head {
  display: flex;
  flex-wrap: wrap;
  align-items: flex-start;
  justify-content: space-between;
  gap: 0.75rem 1rem;
}

.artifacts-head h2 {
  margin: 0;
  font-size: var(--font-ui-body);
  font-weight: 600;
}

.artifacts-actions {
  display: flex;
  flex-wrap: wrap;
  align-items: flex-end;
  gap: 0.5rem;
}

.artifacts-field {
  display: flex;
  min-width: 0;
  flex-direction: column;
  gap: 0.25rem;
  color: var(--color-text-muted);
  font-size: var(--font-ui-note);
}

.artifacts-note,
.artifacts-error,
.artifacts-notice,
.artifacts-item-meta,
.artifacts-item-source {
  margin: 0;
  color: var(--color-text-muted);
  font-size: var(--font-ui-note);
  line-height: 1.5;
}

.artifacts-error {
  color: var(--color-danger);
}

.artifacts-notice {
  color: var(--color-text);
}

.artifacts-state {
  padding: 0.75rem 1rem;
  border: 1px solid var(--color-border);
  border-radius: var(--radius-md);
  background: var(--color-surface-2);
}

.artifacts-state-empty {
  text-align: center;
}

.artifacts-grid {
  display: grid;
  gap: 0.75rem;
  grid-template-columns: minmax(13rem, 1fr) minmax(17rem, 1.4fr) minmax(15rem, 1fr);
  align-items: start;
}

.artifacts-column {
  display: flex;
  min-width: 0;
  flex-direction: column;
  gap: 0.5rem;
  padding: 0.75rem;
  border: 1px solid var(--color-border);
  border-radius: var(--radius-md);
  background: var(--color-surface);
}

.artifacts-column-head {
  display: flex;
  flex-wrap: wrap;
  align-items: baseline;
  justify-content: space-between;
  gap: 0.5rem;
}

.artifacts-column-head h3 {
  margin: 0;
  font-size: var(--font-ui-note);
  font-weight: 600;
}

.artifacts-badge {
  padding: 0 0.375rem;
  border: 1px solid var(--color-border);
  border-radius: var(--radius-md);
  color: var(--color-text-muted);
  font-size: var(--font-ui-note);
}

.artifacts-breadcrumb {
  display: flex;
  flex-wrap: wrap;
  gap: 0.25rem;
}

.artifacts-crumb {
  padding: 0;
  border: 0;
  background: none;
  color: var(--color-text-muted);
  font-size: var(--font-ui-note);
  text-decoration: underline;
  cursor: pointer;
}

.artifacts-crumb[aria-current="location"] {
  color: var(--color-text);
  text-decoration: none;
}

.artifacts-entries,
.artifacts-list {
  display: flex;
  min-width: 0;
  flex-direction: column;
  gap: 0.25rem;
  margin: 0;
  padding: 0;
  list-style: none;
}

.artifacts-entry {
  display: flex;
  min-width: 0;
  width: 100%;
  align-items: center;
  gap: 0.375rem;
  padding: 0.25rem 0.375rem;
  border: 1px solid transparent;
  border-radius: var(--radius-md);
  background: none;
  color: var(--color-text);
  font-size: var(--font-ui-note);
  text-align: start;
  cursor: pointer;
}

.artifacts-entry:hover,
.artifacts-entry-selected {
  border-color: var(--color-border);
  background: var(--color-surface-2);
}

.artifacts-entry-name {
  min-width: 0;
  overflow: hidden;
  text-overflow: ellipsis;
  white-space: nowrap;
}

.artifacts-entry-size {
  margin-inline-start: auto;
  color: var(--color-text-muted);
}

.artifacts-facts {
  display: grid;
  gap: 0.25rem;
  margin: 0;
}

.artifacts-facts div {
  display: flex;
  gap: 0.5rem;
  justify-content: space-between;
}

.artifacts-facts dt {
  color: var(--color-text-muted);
  font-size: var(--font-ui-note);
}

.artifacts-facts dd {
  margin: 0;
  font-size: var(--font-ui-note);
  text-align: end;
}

.artifacts-preview-media,
.artifacts-preview-frame {
  width: 100%;
  max-height: 22rem;
  border: 1px solid var(--color-border);
  border-radius: var(--radius-md);
  background: var(--color-surface-2);
  object-fit: contain;
}

.artifacts-preview-frame {
  height: 22rem;
}

.artifacts-preview-text {
  max-height: 22rem;
  margin: 0;
  padding: 0.5rem;
  overflow: auto;
  border: 1px solid var(--color-border);
  border-radius: var(--radius-md);
  background: var(--color-surface-2);
  font-size: var(--font-ui-code);
  white-space: pre-wrap;
}

.artifacts-publish {
  display: flex;
  flex-wrap: wrap;
  align-items: flex-end;
  gap: 0.5rem;
}

.artifacts-item {
  display: flex;
  min-width: 0;
  flex-wrap: wrap;
  align-items: flex-start;
  justify-content: space-between;
  gap: 0.5rem;
  padding: 0.5rem;
  border: 1px solid var(--color-border);
  border-radius: var(--radius-md);
}

.artifacts-item-name {
  display: block;
  font-size: var(--font-ui-note);
  font-weight: 600;
}

.artifacts-item-actions {
  display: flex;
  flex-wrap: wrap;
  align-items: center;
  gap: 0.5rem;
}

.artifacts-link {
  color: var(--color-text);
  font-size: var(--font-ui-note);
  text-decoration: underline;
}

@media (max-width: 75rem) {
  .artifacts-grid {
    grid-template-columns: minmax(0, 1fr);
  }
}
</style>
