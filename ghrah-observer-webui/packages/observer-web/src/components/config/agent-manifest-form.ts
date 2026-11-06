import { dump, load } from "js-yaml";

export type JsonObject = Record<string, unknown>;
export type AbilityMode = "ref" | "type";
export type HookType = "builtin" | "python";
export type HookEvent =
  | "before_action"
  | "after_action"
  | "pre_llm_call"
  | "post_llm_call"
  | "pre_tool_execute"
  | "post_tool_execute"
  | "on_error"
  | "on_max_iterations";

export const hookEvents: HookEvent[] = [
  "before_action",
  "after_action",
  "pre_llm_call",
  "post_llm_call",
  "pre_tool_execute",
  "post_tool_execute",
  "on_error",
  "on_max_iterations",
];

export interface PermissionForm {
  enabled: boolean;
  require_hitl: boolean;
  fs_read_only: boolean;
  fs_write: boolean;
  net_access: boolean;
  shell_access: boolean;
  allowed_paths: string;
  denied_paths: string;
  allowed_commands: string;
  denied_commands: string;
}

export interface AbilityForm {
  mode: AbilityMode;
  value: string;
  permissions: PermissionForm;
}

export interface HookForm {
  type: HookType;
  handler: string;
  source: string;
  params: string;
}

export interface AgentManifestForm {
  namespace: string;
  name: string;
  title: string;
  metadataDescription: string;
  tags: string;
  icon: string;
  deprecated: boolean;
  endpoint_name: string;
  temperature: string;
  max_tokens: string;
  top_p: string;
  top_k: string;
  extra_params: string;
  system_prompt: string;
  max_iterations: string;
  communication_timeout: string;
  runtimeDescription: string;
  abilities: AbilityForm[];
  contextEnabled: boolean;
  windowEnabled: boolean;
  window: {
    max_tokens: string;
    strategies: string;
    tool_call_max_length: string;
    sliding_window_size: string;
    compact_threshold: string;
    compact_keep_recent: string;
    compact_method: string;
  };
  persistenceEnabled: boolean;
  persistence: {
    type: string;
    compress: boolean;
    auto_persist: boolean;
    snapshot_interval: string;
  };
  hooks: Record<HookEvent, HookForm[]>;
}

export function createEmptyAgentManifestForm(): AgentManifestForm {
  return {
    namespace: "",
    name: "",
    title: "",
    metadataDescription: "",
    tags: "",
    icon: "",
    deprecated: false,
    endpoint_name: "",
    temperature: "",
    max_tokens: "",
    top_p: "",
    top_k: "",
    extra_params: "{}",
    system_prompt: "You are a helpful assistant.",
    max_iterations: "10",
    communication_timeout: "300",
    runtimeDescription: "",
    abilities: [{ mode: "type", value: "end_task", permissions: createPermissionForm(null) }],
    contextEnabled: false,
    windowEnabled: false,
    window: {
      max_tokens: "",
      strategies: "",
      tool_call_max_length: "",
      sliding_window_size: "",
      compact_threshold: "",
      compact_keep_recent: "",
      compact_method: "",
    },
    persistenceEnabled: false,
    persistence: {
      type: "",
      compress: false,
      auto_persist: false,
      snapshot_interval: "",
    },
    hooks: Object.fromEntries(hookEvents.map((event) => [event, []])) as unknown as Record<
      HookEvent,
      HookForm[]
    >,
  };
}

export function parseManifestObject(source: string): JsonObject {
  const value = load(source, { json: true });
  if (value === null || typeof value !== "object" || Array.isArray(value)) {
    throw new Error("Manifest source must be a YAML mapping.");
  }
  return Object.assign({}, value) as JsonObject;
}

export function parseAgentManifestForm(source: string): AgentManifestForm {
  const value = parseManifestObject(source);
  const metadata = object(value.metadata);
  const model = object(value.model);
  const context = value.context === null ? null : object(value.context);
  const window =
    context?.window === null || context?.window === undefined ? null : object(context.window);
  const persistence =
    context?.persistence === null || context?.persistence === undefined
      ? null
      : object(context.persistence);
  const hooks = value.hooks === null || value.hooks === undefined ? null : object(value.hooks);
  const abilities = Array.isArray(value.abilities) ? value.abilities : [];

  return {
    namespace: stringField(metadata.namespace),
    name: stringField(metadata.name),
    title: stringField(metadata.title),
    metadataDescription: stringField(metadata.description),
    tags: listField(metadata.tags).join(", "),
    icon: stringField(metadata.icon),
    deprecated: metadata.deprecated === true,
    endpoint_name: stringField(model.endpoint_name),
    temperature:
      model.temperature === null || model.temperature === undefined
        ? ""
        : String(model.temperature),
    max_tokens: optionalString(model.max_tokens),
    top_p: optionalString(model.top_p),
    top_k: optionalString(model.top_k),
    extra_params: JSON.stringify(objectOrEmpty(model.extra_params), null, 2),
    system_prompt: stringField(value.system_prompt),
    max_iterations: optionalString(value.max_iterations) || "10",
    communication_timeout: optionalString(value.communication_timeout) || "300",
    runtimeDescription: stringField(value.description),
    abilities: abilities.map((item) => {
      const ability = object(item);
      const mode = typeof ability.ref === "string" && ability.ref ? "ref" : "type";
      return {
        mode,
        value: stringField(mode === "ref" ? ability.ref : ability.type),
        permissions: createPermissionForm(ability.permissions),
      };
    }),
    contextEnabled: context !== null,
    windowEnabled: window !== null,
    window: {
      max_tokens: optionalString(window?.max_tokens),
      strategies: listField(window?.strategies).join(", "),
      tool_call_max_length: optionalString(window?.tool_call_max_length),
      sliding_window_size: optionalString(window?.sliding_window_size),
      compact_threshold: optionalString(window?.compact_threshold),
      compact_keep_recent: optionalString(window?.compact_keep_recent),
      compact_method: optionalString(window?.compact_method),
    },
    persistenceEnabled: persistence !== null,
    persistence: {
      type: optionalString(persistence?.type),
      compress: persistence?.compress === true,
      auto_persist: persistence?.auto_persist === true,
      snapshot_interval: optionalString(persistence?.snapshot_interval),
    },
    hooks: Object.fromEntries(
      hookEvents.map((event) => {
        const items = hooks?.[event];
        return [
          event,
          Array.isArray(items)
            ? items.map((item) => {
                const hook = object(item);
                return {
                  type: hook.type === "python" ? "python" : "builtin",
                  handler: optionalString(hook.handler),
                  source: optionalString(hook.source),
                  params: JSON.stringify(objectOrEmpty(hook.params), null, 2),
                };
              })
            : [],
        ];
      }),
    ) as Record<HookEvent, HookForm[]>,
  };
}

export function agentManifestFormToObject(form: AgentManifestForm): JsonObject {
  const value: JsonObject = {
    manifest: "agent",
    version: "2",
    metadata: {
      namespace: required(form.namespace, "metadata.namespace"),
      name: required(form.name, "metadata.name"),
      title: form.title,
      description: form.metadataDescription,
      tags: splitList(form.tags),
      icon: form.icon,
      deprecated: form.deprecated,
    },
    model: {
      endpoint_name: required(form.endpoint_name, "model.endpoint_name"),
      ...optionalNumber(form.temperature, "model.temperature"),
      ...optionalNumber(form.max_tokens, "model.max_tokens"),
      ...optionalNumber(form.top_p, "model.top_p"),
      ...optionalNumber(form.top_k, "model.top_k"),
      extra_params: parseJson(form.extra_params, "model.extra_params"),
    },
    system_prompt: required(form.system_prompt, "system_prompt"),
    max_iterations: numberField(form.max_iterations, "max_iterations", 10),
    communication_timeout: numberField(form.communication_timeout, "communication_timeout", 300),
    description: form.runtimeDescription,
    abilities: form.abilities.map((ability) => ({
      [ability.mode]: required(ability.value, `abilities.${ability.mode}`),
      ...(ability.permissions.enabled
        ? { permissions: permissionsToObject(ability.permissions) }
        : {}),
    })),
  };

  if (form.contextEnabled) {
    value.context = {
      ...(form.windowEnabled
        ? {
            window: {
              ...optionalNumber(form.window.max_tokens, "context.window.max_tokens"),
              ...(form.window.strategies ? { strategies: splitList(form.window.strategies) } : {}),
              ...optionalNumber(
                form.window.tool_call_max_length,
                "context.window.tool_call_max_length",
              ),
              ...optionalNumber(
                form.window.sliding_window_size,
                "context.window.sliding_window_size",
              ),
              ...optionalNumber(form.window.compact_threshold, "context.window.compact_threshold"),
              ...optionalNumber(
                form.window.compact_keep_recent,
                "context.window.compact_keep_recent",
              ),
              ...(form.window.compact_method ? { compact_method: form.window.compact_method } : {}),
            },
          }
        : {}),
      ...(form.persistenceEnabled
        ? {
            persistence: {
              ...(form.persistence.type ? { type: form.persistence.type } : {}),
              compress: form.persistence.compress,
              auto_persist: form.persistence.auto_persist,
              ...optionalNumber(
                form.persistence.snapshot_interval,
                "context.persistence.snapshot_interval",
              ),
            },
          }
        : {}),
    };
  }

  value.hooks = Object.fromEntries(
    hookEvents.map((event) => [
      event,
      form.hooks[event].map((hook) => ({
        type: hook.type,
        ...(hook.handler ? { handler: hook.handler } : {}),
        ...(hook.source ? { source: hook.source } : {}),
        params: parseJson(hook.params, `hooks.${event}.params`),
      })),
    ]),
  );
  return value;
}

export function applyAgentManifestForm(source: string, form: AgentManifestForm): string {
  const value = parseManifestObject(source);
  const next = agentManifestFormToObject(form);
  deepMerge(value, next);
  return dump(value, { lineWidth: 100, noRefs: true, quotingType: '"' });
}

export function dumpAgentManifestForm(form: AgentManifestForm): string {
  return `${dump(agentManifestFormToObject(form), {
    lineWidth: 100,
    noRefs: true,
    quotingType: '"',
  })}\n`;
}

function createPermissionForm(value: unknown): PermissionForm {
  const permissions = value === null || value === undefined ? null : object(value);
  return {
    enabled: permissions !== null,
    require_hitl: permissions?.require_hitl === true,
    fs_read_only: permissions?.fs_read_only === true,
    fs_write: permissions?.fs_write === true,
    net_access: permissions?.net_access === true,
    shell_access: permissions?.shell_access === true,
    allowed_paths: listField(permissions?.allowed_paths).join(", "),
    denied_paths: listField(permissions?.denied_paths).join(", "),
    allowed_commands: listField(permissions?.allowed_commands).join(", "),
    denied_commands: listField(permissions?.denied_commands).join(", "),
  };
}

function permissionsToObject(form: PermissionForm): JsonObject {
  return {
    require_hitl: form.require_hitl,
    fs_read_only: form.fs_read_only,
    fs_write: form.fs_write,
    net_access: form.net_access,
    shell_access: form.shell_access,
    allowed_paths: splitList(form.allowed_paths),
    denied_paths: splitList(form.denied_paths),
    allowed_commands: splitList(form.allowed_commands),
    denied_commands: splitList(form.denied_commands),
  };
}

function deepMerge(target: JsonObject, patch: JsonObject): void {
  for (const [key, next] of Object.entries(patch)) {
    const current = target[key];
    if (
      next !== null &&
      typeof next === "object" &&
      !Array.isArray(next) &&
      current !== null &&
      typeof current === "object" &&
      !Array.isArray(current)
    ) {
      deepMerge(current as JsonObject, next as JsonObject);
    } else {
      target[key] = JSON.parse(JSON.stringify(next)) as unknown;
    }
  }
}

function object(value: unknown): JsonObject {
  if (value === null || typeof value !== "object" || Array.isArray(value)) return {};
  return Object.fromEntries(Object.entries(value));
}

function objectOrEmpty(value: unknown): JsonObject {
  if (value === null || typeof value !== "object" || Array.isArray(value)) return {};
  return Object.fromEntries(Object.entries(value));
}

function stringField(value: unknown): string {
  return typeof value === "string" ? value : "";
}

function optionalString(value: unknown): string {
  if (value === null || value === undefined || typeof value === "object") return "";
  return String(value);
}

function listField(value: unknown): string[] {
  return Array.isArray(value)
    ? value.filter((item): item is string => typeof item === "string")
    : [];
}

function splitList(value: string): string[] {
  if (typeof value !== "string") return [];
  return value
    .split(",")
    .map((item) => item.trim())
    .filter(Boolean);
}

function required(value: string, field: string): string {
  if (typeof value !== "string" || !value.trim()) throw new Error(`${field} is required.`);
  const trimmed = value.trim();
  return trimmed;
}

function optionalNumber(value: string, field: string): JsonObject {
  if (typeof value !== "string" || !value.trim()) return {};
  const parsed = Number(value);
  if (!Number.isFinite(parsed)) throw new Error(`${field} must be a finite number.`);
  return { [field.split(".").at(-1) as string]: parsed };
}

function numberField(value: string, field: string, fallback: number): number {
  if (typeof value !== "string" || !value.trim()) return fallback;
  const parsed = Number(value);
  if (!Number.isFinite(parsed)) throw new Error(`${field} must be a finite number.`);
  return parsed;
}

function parseJson(value: string, field: string): JsonObject {
  try {
    const parsed = JSON.parse(value || "{}") as unknown;
    if (parsed === null || typeof parsed !== "object" || Array.isArray(parsed)) {
      throw new Error("must be an object");
    }
    return parsed as JsonObject;
  } catch (cause) {
    const error = new Error(`${field} must contain a JSON object: ${(cause as Error).message}`);
    Object.defineProperty(error, "cause", { value: cause, enumerable: false, writable: false });
    throw error;
  }
}
