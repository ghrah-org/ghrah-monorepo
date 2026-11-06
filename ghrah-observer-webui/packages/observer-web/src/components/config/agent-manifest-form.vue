<script setup lang="ts">
/* eslint-disable vue/no-mutating-props -- This stateful control intentionally edits nested fields of one reactive form object. */
import { useI18n } from "vue-i18n";
import {
  hookEvents,
  type AgentManifestForm,
  type HookEvent,
  type PermissionForm,
} from "./agent-manifest-form";

defineProps<{
  form: AgentManifestForm;
  lockIdentity?: boolean;
  disabled?: boolean;
}>();

defineEmits<{
  addAbility: [];
  removeAbility: [index: number];
  addHook: [event: HookEvent];
  removeHook: [event: HookEvent, index: number];
}>();

const { t } = useI18n();

function abilityPermissionKey(index: number, field: keyof PermissionForm): string {
  return `ability-${index}-${String(field)}`;
}
</script>

<template>
  <div class="agent-manifest-form">
    <fieldset :disabled="disabled">
      <legend>{{ t("config.manifestForm.identity") }}</legend>
      <p>{{ t("config.manifestForm.descriptionHint") }}</p>
      <div class="fields-2">
        <label>
          <span>{{ t("config.manifest.namespace") }}</span>
          <input
            v-model.trim="form.namespace"
            data-autofocus
            data-field="namespace"
            type="text"
            required
            pattern="^[a-z][a-z0-9_.]*[a-z0-9]$"
            :disabled="lockIdentity"
          />
        </label>
        <label>
          <span>{{ t("config.manifest.name") }}</span>
          <input
            v-model.trim="form.name"
            data-field="name"
            type="text"
            required
            pattern="^[a-z][a-z0-9_]*$"
            :disabled="lockIdentity"
          />
        </label>
        <label>
          <span>{{ t("config.manifest.titleField") }}</span>
          <input v-model="form.title" data-field="title" type="text" />
        </label>
        <label>
          <span>{{ t("config.manifest.tags") }}</span>
          <input v-model="form.tags" type="text" />
        </label>
        <label>
          <span>{{ t("config.manifest.icon") }}</span>
          <input v-model="form.icon" type="text" />
        </label>
        <label class="checkbox">
          <input v-model="form.deprecated" data-field="deprecated" type="checkbox" />
          <span>{{ t("config.manifest.deprecated") }}</span>
        </label>
      </div>
      <label>
        <span>{{ t("config.manifest.metadataDescription") }}</span>
        <textarea v-model="form.metadataDescription" data-field="metadata-description" rows="2" />
      </label>
    </fieldset>

    <fieldset :disabled="disabled">
      <legend>{{ t("config.manifest.model") }}</legend>
      <div class="fields-2">
        <label>
          <span>{{ t("config.manifest.endpointName") }}</span>
          <input
            v-model.trim="form.endpoint_name"
            data-field="endpoint-name"
            type="text"
            required
          />
        </label>
        <label>
          <span>{{ t("config.manifest.maxIterations") }}</span>
          <input v-model="form.max_iterations" type="number" step="1" min="-1" required />
        </label>
        <label>
          <span>{{ t("config.manifest.temperature") }}</span>
          <input v-model="form.temperature" type="number" step="0.1" min="0" max="2" />
        </label>
        <label>
          <span>{{ t("config.manifest.maxTokens") }}</span>
          <input v-model="form.max_tokens" type="number" step="1" min="1" />
        </label>
        <label>
          <span>{{ t("config.manifest.topP") }}</span>
          <input v-model="form.top_p" data-field="top-p-field" type="text" inputmode="decimal" />
        </label>
        <label>
          <span>{{ t("config.manifest.topK") }}</span>
          <input v-model="form.top_k" type="number" step="1" min="1" />
        </label>
      </div>
      <label>
        <span>{{ t("config.manifest.extraParams") }}</span>
        <textarea
          v-model="form.extra_params"
          data-field="extra-params"
          rows="4"
          spellcheck="false"
        />
      </label>
    </fieldset>

    <fieldset :disabled="disabled">
      <legend>{{ t("config.manifest.runtime") }}</legend>
      <label>
        <span>{{ t("config.manifest.systemPrompt") }}</span>
        <textarea v-model="form.system_prompt" data-field="system-prompt" rows="6" required />
      </label>
      <div class="fields-2">
        <label>
          <span>{{ t("config.manifest.communicationTimeout") }}</span>
          <input v-model="form.communication_timeout" type="number" step="0.1" min="-1" required />
        </label>
        <label class="checkbox">
          <input v-model="form.contextEnabled" data-field="context-enabled" type="checkbox" />
          <span>{{ t("config.manifestForm.contextEnabled") }}</span>
        </label>
      </div>
      <label>
        <span>{{ t("config.manifest.runtimeDescription") }}</span>
        <textarea v-model="form.runtimeDescription" data-field="runtime-description" rows="2" />
      </label>
    </fieldset>

    <fieldset :disabled="disabled">
      <legend>{{ t("config.manifest.abilities") }}</legend>
      <p>{{ t("config.manifest.abilitiesHint") }}</p>
      <section v-for="(ability, index) in form.abilities" :key="`ability-${index}`">
        <div class="row">
          <select v-model="ability.mode">
            <option value="type">{{ t("config.manifest.typeBuiltIn") }}</option>
            <option value="ref">{{ t("config.manifest.ref") }}</option>
          </select>
          <input v-model.trim="ability.value" type="text" required />
          <button
            type="button"
            class="btn-secondary"
            :disabled="form.abilities.length <= 1"
            @click="$emit('removeAbility', index)"
          >
            {{ t("config.manifestForm.removeAbility") }}
          </button>
        </div>
        <label class="checkbox">
          <input v-model="ability.permissions.enabled" type="checkbox" />
          <span>{{ t("config.manifestForm.permissionOverride") }}</span>
        </label>
        <div v-if="ability.permissions.enabled" class="permissions">
          <div class="fields-2">
            <label
              v-for="field in [
                'require_hitl',
                'fs_read_only',
                'fs_write',
                'net_access',
                'shell_access',
              ] as const"
              :key="abilityPermissionKey(index, field)"
              class="checkbox"
            >
              <input v-model="ability.permissions[field]" type="checkbox" />
              <span>{{ t(`config.permission.${field}`) }}</span>
            </label>
          </div>
          <div class="fields-2">
            <label>
              <span>{{ t("config.permission.allowed_paths") }}</span>
              <input v-model="ability.permissions.allowed_paths" type="text" />
            </label>
            <label>
              <span>{{ t("config.permission.denied_paths") }}</span>
              <input v-model="ability.permissions.denied_paths" type="text" />
            </label>
            <label>
              <span>{{ t("config.permission.allowed_commands") }}</span>
              <input v-model="ability.permissions.allowed_commands" type="text" />
            </label>
            <label>
              <span>{{ t("config.permission.denied_commands") }}</span>
              <input v-model="ability.permissions.denied_commands" type="text" />
            </label>
          </div>
        </div>
      </section>
      <button type="button" class="btn-secondary" @click="$emit('addAbility')">
        {{ t("config.manifest.addAbility") }}
      </button>
    </fieldset>

    <fieldset v-if="form.contextEnabled" :disabled="disabled">
      <legend>{{ t("config.manifestForm.context") }}</legend>
      <div class="fields-2">
        <label class="checkbox">
          <input v-model="form.windowEnabled" type="checkbox" />
          <span>{{ t("config.manifestForm.windowEnabled") }}</span>
        </label>
        <label class="checkbox">
          <input v-model="form.persistenceEnabled" type="checkbox" />
          <span>{{ t("config.manifestForm.persistenceEnabled") }}</span>
        </label>
      </div>
      <template v-if="form.windowEnabled">
        <h4>{{ t("config.manifestForm.window") }}</h4>
        <div class="fields-2">
          <label>
            <span>{{ t("config.context.max_tokens") }}</span>
            <input v-model="form.window.max_tokens" type="number" step="1" min="1" />
          </label>
          <label>
            <span>{{ t("config.context.strategies") }}</span>
            <input v-model="form.window.strategies" type="text" />
          </label>
          <label>
            <span>{{ t("config.context.tool_call_max_length") }}</span>
            <input v-model="form.window.tool_call_max_length" type="number" step="1" min="0" />
          </label>
          <label>
            <span>{{ t("config.context.sliding_window_size") }}</span>
            <input v-model="form.window.sliding_window_size" type="number" step="1" min="1" />
          </label>
          <label>
            <span>{{ t("config.context.compact_threshold") }}</span>
            <input v-model="form.window.compact_threshold" type="number" step="0.01" />
          </label>
          <label>
            <span>{{ t("config.context.compact_keep_recent") }}</span>
            <input v-model="form.window.compact_keep_recent" type="number" step="1" min="0" />
          </label>
          <label>
            <span>{{ t("config.context.compact_method") }}</span>
            <input v-model="form.window.compact_method" type="text" />
          </label>
        </div>
      </template>
      <template v-if="form.persistenceEnabled">
        <h4>{{ t("config.manifestForm.persistence") }}</h4>
        <div class="fields-2">
          <label>
            <span>{{ t("config.context.type") }}</span>
            <input v-model="form.persistence.type" type="text" />
          </label>
          <label>
            <span>{{ t("config.context.snapshot_interval") }}</span>
            <input v-model="form.persistence.snapshot_interval" type="number" step="1" min="0" />
          </label>
          <label class="checkbox">
            <input v-model="form.persistence.compress" type="checkbox" />
            <span>{{ t("config.context.compress") }}</span>
          </label>
          <label class="checkbox">
            <input v-model="form.persistence.auto_persist" type="checkbox" />
            <span>{{ t("config.context.auto_persist") }}</span>
          </label>
        </div>
      </template>
    </fieldset>

    <fieldset :disabled="disabled">
      <legend>{{ t("config.manifestForm.hooks") }}</legend>
      <details v-for="event in hookEvents" :key="event">
        <summary>{{ t(`config.hook.${event}`) }} ({{ form.hooks[event].length }})</summary>
        <section v-for="(hook, index) in form.hooks[event]" :key="`${event}-${index}`">
          <div class="fields-2">
            <label>
              <span>{{ t("config.manifestForm.hookType") }}</span>
              <select v-model="hook.type">
                <option value="builtin">{{ t("config.hook.builtin") }}</option>
                <option value="python">{{ t("config.hook.python") }}</option>
              </select>
            </label>
            <label>
              <span>{{ t("config.manifestForm.hookHandler") }}</span>
              <input v-model="hook.handler" type="text" />
            </label>
            <label>
              <span>{{ t("config.manifestForm.hookSource") }}</span>
              <input v-model="hook.source" type="text" />
            </label>
            <label>
              <span>{{ t("config.manifestForm.hookParams") }}</span>
              <textarea v-model="hook.params" rows="2" spellcheck="false" />
            </label>
          </div>
          <button type="button" class="btn-secondary" @click="$emit('removeHook', event, index)">
            {{ t("config.manifestForm.removeHook") }}
          </button>
        </section>
        <button type="button" class="btn-secondary" @click="$emit('addHook', event)">
          {{ t("config.manifestForm.addHook") }}
        </button>
      </details>
    </fieldset>
  </div>
</template>

<style scoped>
.agent-manifest-form {
  display: grid;
  gap: var(--spacing-4);
}
.agent-manifest-form fieldset,
.agent-manifest-form label,
.agent-manifest-form textarea,
.agent-manifest-form input,
.agent-manifest-form select,
.agent-manifest-form section,
.agent-manifest-form details,
.agent-manifest-form p {
  min-width: 0;
}
.agent-manifest-form fieldset,
.agent-manifest-form section {
  display: grid;
  gap: var(--spacing-2);
}
.agent-manifest-form label {
  display: grid;
  gap: var(--spacing-1);
  font-size: var(--font-ui-body);
}
.agent-manifest-form label.checkbox {
  grid-template-columns: auto 1fr;
  align-items: center;
}
.agent-manifest-form .fields-2 {
  display: grid;
  grid-template-columns: repeat(2, minmax(0, 1fr));
  gap: var(--spacing-2);
}
.agent-manifest-form .row {
  display: grid;
  grid-template-columns: minmax(9rem, 1fr) minmax(0, 4fr) auto;
  gap: var(--spacing-2);
}
.agent-manifest-form .permissions {
  display: grid;
  gap: var(--spacing-2);
  padding: var(--spacing-2);
  border: 1px solid currentColor;
  border-radius: var(--radius-md);
}
.agent-manifest-form h4 {
  margin: 0;
  font-size: var(--font-ui-body);
}
.agent-manifest-form details {
  border: 1px solid currentColor;
  border-radius: var(--radius-md);
  padding: var(--spacing-2);
}
.agent-manifest-form summary {
  cursor: pointer;
}
@media (max-width: 45rem) {
  .agent-manifest-form .fields-2,
  .agent-manifest-form .row {
    grid-template-columns: 1fr;
  }
}
</style>
