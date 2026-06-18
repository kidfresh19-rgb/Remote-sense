import { ArrowsOut, MagnifyingGlass, PencilSimple, Plant, Upload, X } from "@phosphor-icons/react";
import type { Geometry } from "geojson";
import { useEffect, useRef, useState } from "react";

import { cn } from "@/lib/format";
import { geocodeSearch, type GeocodingResult } from "@/lib/geocode";

import { IconButton } from "./ui";

/** A result carries a usable AOI boundary only when its geometry is an (Multi)Polygon; a point/node
 *  result just recenters the map. The badge in the dropdown surfaces this so the analyst knows which
 *  results set an AOI before clicking. */
function resultHasBoundary(r: GeocodingResult): boolean {
  return !!r.geojson && (r.geojson.type === "Polygon" || r.geojson.type === "MultiPolygon");
}

interface AOIBarProps {
  onDrawToggle: () => void;
  drawActive: boolean;
  onCancelDraw: () => void;
  onOpenCoords: () => void;
  onOpenUpload: () => void;
  /** When provided, shows a button that opens the farm/field picker (AOI Studio only). */
  onOpenFarms?: () => void;
  onAOISet: (geometry: Geometry, label: string) => void;
  onFlyTo: (center: [number, number], zoom?: number) => void;
}

export function AOIBar({
  onDrawToggle,
  drawActive,
  onCancelDraw,
  onOpenCoords,
  onOpenUpload,
  onOpenFarms,
  onAOISet,
  onFlyTo,
}: AOIBarProps) {
  const [query, setQuery] = useState("");
  const [results, setResults] = useState<GeocodingResult[]>([]);
  const [loading, setLoading] = useState(false);
  const [open, setOpen] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const abortRef = useRef<AbortController | null>(null);
  const debounceRef = useRef<ReturnType<typeof setTimeout> | null>(null);
  const inputRef = useRef<HTMLInputElement | null>(null);
  const containerRef = useRef<HTMLDivElement | null>(null);

  // Debounced geocode search — cancel in-flight requests on each new keystroke.
  useEffect(() => {
    if (debounceRef.current) clearTimeout(debounceRef.current);
    if (!query.trim()) {
      setResults([]);
      setOpen(false);
      setLoading(false);
      setError(null);
      return;
    }

    debounceRef.current = setTimeout(async () => {
      abortRef.current?.abort();
      const controller = new AbortController();
      abortRef.current = controller;
      setLoading(true);
      setError(null);
      try {
        const hits = await geocodeSearch(query, controller.signal);
        if (controller.signal.aborted) return; // a newer keystroke superseded this run
        setResults(hits);
        setOpen(true); // open even with no hits so the empty-state message shows
      } catch {
        if (controller.signal.aborted) return;
        setResults([]);
        setError("Search failed. Check your connection and try again.");
        setOpen(true);
      } finally {
        if (!controller.signal.aborted) setLoading(false);
      }
    }, 400);

    return () => {
      if (debounceRef.current) clearTimeout(debounceRef.current);
    };
  }, [query]);

  // Close dropdown when clicking outside the bar.
  useEffect(() => {
    function handler(e: MouseEvent) {
      if (containerRef.current && !containerRef.current.contains(e.target as Node)) {
        setOpen(false);
      }
    }
    document.addEventListener("mousedown", handler);
    return () => document.removeEventListener("mousedown", handler);
  }, []);

  function handleSelect(result: GeocodingResult) {
    onFlyTo([parseFloat(result.lon), parseFloat(result.lat)], 13);
    if (
      result.geojson &&
      (result.geojson.type === "Polygon" || result.geojson.type === "MultiPolygon")
    ) {
      onAOISet(result.geojson, result.display_name.split(",")[0].trim());
    }
    setOpen(false);
    setQuery("");
  }

  function handleKeyDown(e: React.KeyboardEvent<HTMLInputElement>) {
    if (e.key === "Escape") {
      setOpen(false);
      inputRef.current?.blur();
    }
  }

  function handleClear() {
    setQuery("");
    setResults([]);
    setOpen(false);
    setError(null);
    inputRef.current?.focus();
  }

  return (
    <div ref={containerRef} className="relative w-full">
      {/* Main bar row */}
      <div className="flex items-center gap-1.5 rounded-lg border border-border bg-panel/90 px-2 py-1.5 shadow-sm backdrop-blur-sm">
        {/* Search input */}
        <MagnifyingGlass
          size={15}
          className={cn("shrink-0 transition-colors", loading ? "text-accent" : "text-muted")}
        />
        <input
          ref={inputRef}
          type="text"
          value={query}
          onChange={(e) => setQuery(e.target.value)}
          onKeyDown={handleKeyDown}
          onFocus={() => results.length > 0 && setOpen(true)}
          placeholder="Search location..."
          className="min-w-0 flex-1 bg-transparent text-sm text-fg placeholder:text-muted focus:outline-none"
          aria-label="Search location"
          aria-autocomplete="list"
          aria-expanded={open}
        />
        {/* Inline loading spinner */}
        {loading && (
          <span className="shrink-0">
            <svg
              className="h-3.5 w-3.5 animate-spin text-accent"
              viewBox="0 0 24 24"
              fill="none"
              stroke="currentColor"
              strokeWidth={3}
            >
              <circle cx={12} cy={12} r={10} strokeOpacity={0.25} />
              <path d="M12 2a10 10 0 0 1 10 10" strokeLinecap="round" />
            </svg>
          </span>
        )}
        {/* Clear button */}
        {query && !loading && (
          <button
            onClick={handleClear}
            aria-label="Clear search"
            className="shrink-0 text-muted transition-colors hover:text-fg"
          >
            <X size={13} />
          </button>
        )}

        {/* Divider */}
        <div className="mx-0.5 h-5 w-px shrink-0 bg-border" />

        {/* Draw toggle */}
        <IconButton
          label={drawActive ? "Cancel draw" : "Draw polygon"}
          active={drawActive}
          onClick={drawActive ? onCancelDraw : onDrawToggle}
          className="size-7 shrink-0 border border-border bg-panel/80 text-xs"
        >
          {drawActive ? <X size={14} /> : <PencilSimple size={14} />}
        </IconButton>

        {/* Coordinate entry */}
        <IconButton
          label="Enter coordinates"
          onClick={onOpenCoords}
          className="size-7 shrink-0 border border-border bg-panel/80"
        >
          <ArrowsOut size={14} />
        </IconButton>

        {/* File upload */}
        <IconButton
          label="Upload boundary file"
          onClick={onOpenUpload}
          className="size-7 shrink-0 border border-border bg-panel/80"
        >
          <Upload size={14} />
        </IconButton>

        {/* Existing farm / field picker (AOI Studio) */}
        {onOpenFarms ? (
          <IconButton
            label="Use an existing farm or field"
            onClick={onOpenFarms}
            className="size-7 shrink-0 border border-border bg-panel/80"
          >
            <Plant size={14} />
          </IconButton>
        ) : null}
      </div>

      {/* Draw hint */}
      {drawActive && (
        <p className="mt-1 text-center text-[11px] text-accent">
          Click to add vertices. Double-click or Enter to finish, Backspace to undo, Esc to cancel.
        </p>
      )}

      {/* Geocoder results dropdown — results, a no-matches note, or a failure message. */}
      {open && (
        <div className="absolute inset-x-0 top-full z-50 mt-1 overflow-hidden rounded-b-lg border border-border bg-panel shadow-lg">
          {error ? (
            <p className="px-3 py-2 text-sm text-critical">{error}</p>
          ) : results.length > 0 ? (
            <ul role="listbox" aria-label="Location suggestions">
              {results.map((result) => {
                const boundary = resultHasBoundary(result);
                return (
                  <li key={result.place_id} role="option" aria-selected={false}>
                    <button
                      onMouseDown={(e) => {
                        // Use mousedown so it fires before the input's blur event.
                        e.preventDefault();
                        handleSelect(result);
                      }}
                      className="flex w-full items-center gap-2 px-3 py-2 text-left text-sm transition-colors hover:bg-panel-2 focus:bg-panel-2 focus:outline-none"
                    >
                      <span className="min-w-0 flex-1 truncate text-fg">{result.display_name}</span>
                      <span className="shrink-0 rounded-full bg-panel-2 px-1.5 py-0.5 text-[10px] uppercase tracking-wide text-muted">
                        {result.type}
                      </span>
                      <span
                        title={boundary ? "Sets a custom AOI boundary" : "Recenters the map only"}
                        className={cn(
                          "shrink-0 rounded-full px-1.5 py-0.5 text-[10px] uppercase tracking-wide",
                          boundary ? "bg-accent/15 text-accent" : "bg-panel-2 text-muted",
                        )}
                      >
                        {boundary ? "area" : "point"}
                      </span>
                    </button>
                  </li>
                );
              })}
            </ul>
          ) : !loading ? (
            <p className="px-3 py-2 text-sm text-muted">
              No matches for <span className="text-fg">{query}</span>
            </p>
          ) : null}
        </div>
      )}
    </div>
  );
}
