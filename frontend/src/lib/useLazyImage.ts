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
  const loadedRef = useRef(false);

  useEffect(() => {
    setSrc(null);
    loadedRef.current = false;
  }, [url]);

  const ref = useCallback(
    (el: Element | null) => {
      if (observerRef.current) {
        observerRef.current.disconnect();
        observerRef.current = null;
      }
      if (!el || !url || loadedRef.current) return;
      const observer = new IntersectionObserver(
        ([entry]) => {
          if (entry.isIntersecting) {
            setSrc(url);
            loadedRef.current = true;
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
