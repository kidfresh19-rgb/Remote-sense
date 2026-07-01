import { useCallback, useEffect, useRef, useState } from "react";

/**
 * IntersectionObserver-based lazy image loader. Returns a `ref` to attach to a placeholder
 * element and a `src` that becomes the URL once the element enters the viewport. Only issues
 * the network request when the element is visible, so long lists do not flood the server.
 */
export function useLazyImage(url: string | null): {
  ref: (el: Element | null) => void;
  src: string | null;
} {
  const [src, setSrc] = useState<string | null>(null);
  const observerRef = useRef<IntersectionObserver | null>(null);

  useEffect(() => {
    setSrc(null);
  }, [url]);

  // `ref` is memoized on `url`, so React only re-invokes it (detach with null, then attach with
  // the element) when `url` actually changes - never on an unrelated re-render of the same url.
  // A "haven't we already loaded this?" guard is therefore unnecessary and was actively harmful:
  // it fired during the layout-phase attach, before the effect above (a passive effect, which
  // always runs later) could reset it, so it read a stale flag from the *previous* url and
  // skipped observing the new one - leaving an already-loaded, still-visible thumbnail stuck
  // blank on a url change instead of loading the new image. Always observing fresh here is cheap
  // and correct; the browser's own HTTP cache (not this hook) is what avoids refetching a url
  // already seen.
  const ref = useCallback(
    (el: Element | null) => {
      if (observerRef.current) {
        observerRef.current.disconnect();
        observerRef.current = null;
      }
      if (!el || !url) return;
      const observer = new IntersectionObserver(
        ([entry]) => {
          if (entry.isIntersecting) {
            setSrc(url);
            observer.disconnect();
          }
        },
        { rootMargin: "120px" },
      );
      observer.observe(el);
      observerRef.current = observer;
    },
    [url],
  );

  return { ref, src };
}
