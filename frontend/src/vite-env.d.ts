/// <reference types="vite/client" />

declare module "@fontsource-variable/outfit";
declare module "@fontsource-variable/jetbrains-mono";

interface ImportMetaEnv {
  readonly VITE_API_BASE_URL?: string;
  readonly VITE_TILER_BASE_URL?: string;
  readonly VITE_BASEMAP_URL?: string;
  readonly VITE_DEV_TOKEN?: string;
}

interface ImportMeta {
  readonly env: ImportMetaEnv;
}
