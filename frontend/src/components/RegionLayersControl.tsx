import { Check, MapTrifold } from "@phosphor-icons/react";
import { useEffect, useRef, useState } from "react";

import type { RegionLayer } from "@/lib/api";

import { IconButton } from "./ui";

// Mirror the overlay hues in useFieldMap.ts so the swatches match what is drawn on the map.
const NR_COLOR = "#f0b429";
const UPLOADED_COLOR = "#9b8afb";

interface RegionLayersControlProps {
  layers: RegionLayer[];
  naturalRegionsOn: boolean;
  onToggleNaturalRegions: () => void;
  uploadedLayerId: string | null;
  onSelectUploaded: (layerId: string | null) => void;
}

/** Map control for the region-boundary overlays (comparison groups, PRD 0002 slices 8a/8b). A
 *  popover lists the seeded Natural Region layer (a toggle) and the analyst-uploaded layers (a
 *  single choice, click again to hide). Both overlays default off; the colour swatches double as
 *  the legend for what is drawn on the map. */
export function RegionLayersControl({
  layers,
  naturalRegionsOn,
  onToggleNaturalRegions,
  uploadedLayerId,
  onSelectUploaded,
}: RegionLayersControlProps) {
  const [open, setOpen] = useState(false);
  const wrapperRef = useRef<HTMLDivElement | null>(null);

  // Dismiss on outside click or Escape, like a standard popover.
  useEffect(() => {
    if (!open) return;
    const onPointerDown = (e: MouseEvent) => {
      if (wrapperRef.current && !wrapperRef.current.contains(e.target as Node)) setOpen(false);
    };
    const onKeyDown = (e: KeyboardEvent) => {
      if (e.key === "Escape") setOpen(false);
    };
    document.addEventListener("mousedown", onPointerDown);
    document.addEventListener("keydown", onKeyDown);
    return () => {
      document.removeEventListener("mousedown", onPointerDown);
      document.removeEventListener("keydown", onKeyDown);
    };
  }, [open]);

  const seeded = layers.find((l) => l.kind === "seeded") ?? null;
  const uploaded = layers.filter((l) => l.kind === "uploaded" || l.kind === "drawn");
  const anyOn = naturalRegionsOn || uploadedLayerId !== null;
  const hasLayers = layers.length > 0;

  return (
    <div ref={wrapperRef} className="relative">
      <IconButton
        label="Region boundary layers"
        active={anyOn}
        onClick={() => setOpen((v) => !v)}
        disabled={!hasLayers}
        aria-expanded={open}
        className="border border-border bg-panel"
      >
        <MapTrifold size={18} />
      </IconButton>

      {open && (
        <div className="absolute left-full top-0 ml-2 w-64 rounded-lg border border-border bg-panel p-2 shadow-lg">
          <p className="px-1.5 pb-1.5 text-xs font-medium text-muted">Region boundaries</p>

          {seeded && (
            <LayerRow
              color={NR_COLOR}
              name={seeded.name}
              count={seeded.region_count}
              selected={naturalRegionsOn}
              onClick={onToggleNaturalRegions}
            />
          )}

          <p className="mt-1.5 px-1.5 pb-1 pt-1.5 text-[11px] uppercase tracking-wide text-muted">
            Uploaded layers
          </p>
          {uploaded.length === 0 ? (
            <p className="px-1.5 pb-1 text-xs text-muted">No uploaded layers yet.</p>
          ) : (
            uploaded.map((layer) => (
              <LayerRow
                key={layer.layer_id}
                color={UPLOADED_COLOR}
                name={layer.name}
                count={layer.region_count}
                selected={uploadedLayerId === layer.layer_id}
                onClick={() =>
                  onSelectUploaded(uploadedLayerId === layer.layer_id ? null : layer.layer_id)
                }
              />
            ))
          )}
        </div>
      )}
    </div>
  );
}

function LayerRow({
  color,
  name,
  count,
  selected,
  onClick,
}: {
  color: string;
  name: string;
  count: number;
  selected: boolean;
  onClick: () => void;
}) {
  return (
    <button
      type="button"
      aria-pressed={selected}
      onClick={onClick}
      className="flex w-full items-center gap-2 rounded-md px-1.5 py-1.5 text-left text-sm text-fg hover:bg-panel-2"
    >
      <span
        aria-hidden
        className="size-3 shrink-0 rounded-sm border border-black/20"
        style={{ backgroundColor: color }}
      />
      <span className="min-w-0 flex-1 truncate">{name}</span>
      <span className="shrink-0 text-xs text-muted">{count}</span>
      <Check
        size={14}
        weight="bold"
        className={selected ? "shrink-0 text-accent" : "shrink-0 text-transparent"}
      />
    </button>
  );
}
