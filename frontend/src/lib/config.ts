function trimSlash(url: string): string {
  return url.replace(/\/+$/, "");
}

export const config = {
  apiBaseUrl: trimSlash(import.meta.env.VITE_API_BASE_URL ?? "http://localhost:8000"),
  // nginx fronts both the API and the tiler on 8000 (tiles at /tiles/), so the default tiler
  // origin matches the API origin. Override only when the tiler is exposed on its own host.
  tilerBaseUrl: trimSlash(import.meta.env.VITE_TILER_BASE_URL ?? "http://localhost:8000"),
  basemapUrl: (import.meta.env.VITE_BASEMAP_URL ?? "").trim(),
  devToken: (import.meta.env.VITE_DEV_TOKEN ?? "").trim(),
} as const;
