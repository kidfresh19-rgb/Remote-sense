import { ArrowsOut, MagnifyingGlass, PencilSimple, Upload, X } from "@phosphor-icons/react";
import type { Geometry } from "geojson";
import { useEffect, useRef, useState } from "react";

import { cn } from "@/lib/format";
import { geocodeSearch, type GeocodingResult } from "@/lib/geocode";

import { IconButton } from "./ui";

interface AOIBarProps {
  onDrawToggle: () => void;
  drawActive: boolean;
  onCancelDraw: () => void;
  onOpenCoords: () => void;
  onOpenUpload: () => void;
  onAOISet: (geometry: Geometry, label: string) => void;
  onFlyTo: (center: [number, number], zoom?: number) => void;
}

export function AOIBar({
  onDrawToggle,
  drawActive,
  onCancelDraw,
  onOpenCoords,
  onOpenUpload,
  onAOISet,
  onFlyTo,
}: AOIBarProps) {
  const [query, setQuery] = useState("");
  const [results, setResults] = useState<GeocodingResult[]>([]);
  const [loading, setLoading] = useState(false);
  const [open, setOpen] = useState(false);
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
      return;
    }

    debounceRef.current = setTimeout(async () => {
      abortRef.current?.abort();
      abortRef.current = new AbortController();
      setLoading(true);
      const hits = await geocodeSearch(query, abortRef.current.signal);
      setLoading(false);
      setResults(hits);
      setOpen(hits.length > 0);
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
      </div>

      {/* Draw hint */}
      {drawActive && (
        <p className="mt-1 text-center text-[11px] text-accent">
          Click to add vertices — double-click to finish the polygon
        </p>
      )}

      {/* Geocoder results dropdown */}
      {open && results.length > 0 && (
        <ul
          role="listbox"
          aria-label="Location suggestions"
          className="absolute inset-x-0 top-full z-50 mt-1 overflow-hidden rounded-b-lg border border-border bg-panel shadow-lg"
        >
          {results.map((result) => (
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
              </button>
            </li>
          ))}
        </ul>
      )}
    </div>
  );
}
