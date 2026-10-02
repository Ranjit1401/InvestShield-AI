/// <reference types="vite/client" />

// Vite injects VITE_* variables that are present at build time. Only
// `VITE_API_BASE_URL` is read by the app; see `src/services/api-client.ts`.
interface ImportMetaEnv {
  readonly VITE_API_BASE_URL?: string;
}

interface ImportMeta {
  readonly env: ImportMetaEnv;
}
