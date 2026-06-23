import { useCallback, useEffect, useRef, useState } from "react";
import type { Geometry } from "geojson";

import { config } from "./config";

export interface NaturalColorReq {
  scene_id: string;
  geometry: Geometry;
  pass_date: string;
  // Omitted for the filmstrip thumbnail (server defaults to the JPEG preview). The orthophoto
  // download sets "cog" to receive the georeferenced RGB GeoTIFF instead.
  format?: "jpeg" | "cog";
}

/**
 * IntersectionObserver + POST hook for AOI Studio natural-colour thumbnails. The render is
 * dispatched to the worker only when the element enters the viewport, so a 60-pass filmstrip
 * does not flood the natural-color endpoint on first render. The result is cached as a blob
 * URL which is revoked on unmount or when the request changes.
 */
export function useNaturalColorThumbnail(
  req: NaturalColorReq | null,
  token: string | null,
): {
  ref: (el: Element | null) => void;
  src: string | null;
  loading: boolean;
  error: boolean;
} {
  const [src, setSrc] = useState<string | null>(null);
  const [loading, setLoading] = useState(false);
  const [error, setError] = useState(false);

  const observerRef = useRef<IntersectionObserver | null>(null);
  const abortRef = useRef<AbortController | null>(null);
  const blobUrlRef = useRef<string | null>(null);
  // Keep latest values accessible inside the observer callback without re-creating the observer.
  const reqRef = useRef(req);
  const tokenRef = useRef(token);
  reqRef.current = req;
  tokenRef.current = token;

  // Reset state when the scene/date changes.
  const sceneId = req?.scene_id;
  const passDate = req?.pass_date;
  useEffect(() => {
    setSrc(null);
    setLoading(false);
    setError(false);
    abortRef.current?.abort();
    if (blobUrlRef.current) {
      URL.revokeObjectURL(blobUrlRef.current);
      blobUrlRef.current = null;
    }
  }, [sceneId, passDate]);

  // Cleanup on unmount.
  useEffect(
    () => () => {
      observerRef.current?.disconnect();
      abortRef.current?.abort();
      if (blobUrlRef.current) URL.revokeObjectURL(blobUrlRef.current);
    },
    [],
  );

  const ref = useCallback(
    (el: Element | null) => {
      if (observerRef.current) {
        observerRef.current.disconnect();
        observerRef.current = null;
      }
      if (!el) return;
      const observer = new IntersectionObserver(
        ([entry]) => {
          if (!entry.isIntersecting) return;
          observer.disconnect();
          const r = reqRef.current;
          const t = tokenRef.current;
          if (!r || !t) return;

          setLoading(true);
          setError(false);
          abortRef.current?.abort();
          const ctrl = new AbortController();
          abortRef.current = ctrl;

          fetch(`${config.apiBaseUrl}/analyse/aoi/natural-color`, {
            method: "POST",
            headers: {
              Authorization: `Bearer ${t}`,
              "Content-Type": "application/json",
            },
            body: JSON.stringify(r),
            signal: ctrl.signal,
          })
            .then(async (resp) => {
              if (!resp.ok) throw new Error(`${resp.status}`);
              const blob = await resp.blob();
              if (blobUrlRef.current) URL.revokeObjectURL(blobUrlRef.current);
              const url = URL.createObjectURL(blob);
              blobUrlRef.current = url;
              setSrc(url);
            })
            .catch((err: unknown) => {
              if ((err as Error).name === "AbortError") return;
              setError(true);
            })
            .finally(() => setLoading(false));
        },
        { rootMargin: "120px" },
      );
      observer.observe(el);
      observerRef.current = observer;
    },
    // The observer is re-created only when scene/date/token changes; geometry is read via ref.
    // eslint-disable-next-line react-hooks/exhaustive-deps
    [sceneId, passDate, token],
  );

  return { ref, src, loading, error };
}
