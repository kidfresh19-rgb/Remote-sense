function trimSlash(url: string): string {
  return url.replace(/\/+$/, "");
}

export const config = {
  apiBaseUrl: trimSlash(import.meta.env.VITE_API_BASE_URL ?? "http://localhost:8000"),
  tilerBaseUrl: trimSlash(import.meta.env.VITE_TILER_BASE_URL ?? "http://localhost:8001"),
  basemapUrl: (import.meta.env.VITE_BASEMAP_URL ?? "").trim(),
  devToken: (import.meta.env.VITE_DEV_TOKEN ?? "").trim(),
} as const;
