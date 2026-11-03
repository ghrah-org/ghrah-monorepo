// Local ImportMetaEnv typing for observer-core (no vite dep).
// Vite injects import.meta.env at build/dev time in observer-web; observer-core
// is a library so we declare the minimal shape consumed by stores.
interface ImportMetaEnv {
  readonly VITE_GHRAH_SUBJECT_WS_URL?: string;
}

interface ImportMeta {
  readonly env: ImportMetaEnv;
}

// Global WS URL injected by the ghrah-toolchain static serve (index.html script tag
// preceding the first module script). Declared for DOM-lib consumers; the store
// reads it defensively via a globalThis cast (no DOM lib dependency here).
interface Window {
  readonly __GHRAH_SUBJECT_WS_URL__?: string;
}
