import { computed, ref } from "vue";

const STORAGE_KEY = "ghrah-font-scale";

export const FONT_SCALE_MIN = 0.9;
export const FONT_SCALE_MAX = 1.1;
export const FONT_SCALE_STEP = 0.05;

export const fontScale = ref(1);
export const fontScalePercent = computed(() => Math.round(fontScale.value * 100));

function normalizeScale(value: string | number | null | undefined): number {
  const parsed = typeof value === "number" ? value : Number(value);
  if (!Number.isFinite(parsed)) return 1;
  const clamped = Math.min(FONT_SCALE_MAX, Math.max(FONT_SCALE_MIN, parsed));
  // Snap to the step grid and round away float noise (e.g. 1.1 -> "110.00000000000001%").
  return Number((Math.round(clamped / FONT_SCALE_STEP) * FONT_SCALE_STEP).toFixed(2));
}

// --font-scale feeds the html root font-size (see styles/base.css), so it scales the whole UI.
function applyScale(value: number) {
  document.documentElement.style.setProperty("--font-scale", String(value));
}

export function initFontScale() {
  fontScale.value = normalizeScale(localStorage.getItem(STORAGE_KEY));
  applyScale(fontScale.value);
}

export function setFontScale(value: string | number) {
  fontScale.value = normalizeScale(value);
  localStorage.setItem(STORAGE_KEY, String(fontScale.value));
  applyScale(fontScale.value);
}
