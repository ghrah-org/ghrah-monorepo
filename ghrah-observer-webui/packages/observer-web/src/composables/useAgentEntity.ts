import { useConnectionStore } from "@ghrah/observer-core";
import { computed, onScopeDispose, ref, watch } from "vue";
import { useI18n } from "vue-i18n";
import { useObserver } from "./useObserver";

/** Local request ownership prevents late receipts from overwriting a different selection. */
export function useAgentEntityRequest() {
  const { client } = useObserver();
  const connection = useConnectionStore();
  const { t } = useI18n();
  const busy = ref(false);
  const error = ref("");
  const uncertain = ref(false);
  const connected = computed(() => connection.state === "connected" && client.value !== null);
  let generation = 0;
  let mutating = false;

  function stopWaiting() {
    generation++;
    if (busy.value && mutating) uncertain.value = true;
    busy.value = false;
    error.value = t("agentEntity.waitStopped");
  }
  async function run<T>(work: () => Promise<T>, apply: (value: T) => void, mutation = false) {
    if (busy.value || !connected.value || (mutation && uncertain.value)) return;
    const current = ++generation;
    busy.value = true;
    mutating = mutation;
    error.value = "";
    try {
      const value = await work();
      if (current === generation) apply(value);
    } catch (cause) {
      if (current === generation) {
        error.value = cause instanceof Error ? cause.message : String(cause);
        if (mutation) uncertain.value = true;
      }
    } finally {
      if (current === generation) busy.value = false;
    }
  }
  watch(connected, (value) => {
    if (!value && busy.value) stopWaiting();
  });
  onScopeDispose(() => {
    generation++;
  });
  return { client, connected, busy, error, uncertain, run, stopWaiting };
}
