import { CaretLeft, CaretRight, Columns, Stack, X, Lightning, Eye, Plant } from "@phosphor-icons/react";
import { useEffect, useMemo, useRef, useState } from "react";

import type { Field } from "@/lib/api";
import { saveCustomAOI } from "@/lib/customAOIs";
import { indexMeta, type IndexKey } from "@/lib/indices";
import { useFields, useScenes, useAnalyseAOI } from "@/lib/queries";
import { useWorkspace } from "@/state/workspace";

import { AOIBar } from "./AOIBar";
import { CoordinateEntryModal } from "./CoordinateEntryModal";
import { FileUploadPanel } from "./FileUploadPanel";
import { IndexLegend } from "./IndexLegend";
import { SceneCompare } from "./SceneCompare";
import { EmptyState } from "./states";
import { IconButton } from "./ui";
import { bboxOf, useFieldMap } from "./useFieldMap";

interface MapPanelProps {
  sidebarOpen: boolean;
  onToggleSidebar: () => void;
  inspectorOpen: boolean;
  onToggleInspector: () => void;
}

export function MapPanel({
  sidebarOpen,
  onToggleSidebar,
  inspectorOpen,
  onToggleInspector,
}: MapPanelProps) {
  const {
    farmId,
    fieldId,
    index,
    passDate,
    compareDate,
    showRaster,
    showRgb,
    showFcc,
    toggleRaster,
    toggleRgb,
    toggleFcc,
    setCompareDate,
    customAOI,
    setCustomAOI,
  } = useWorkspace();

  const fields = useFields(farmId);
  const scenes = useScenes(fieldId);

  const [drawMode, setDrawMode] = useState(false);
  const [showCoords, setShowCoords] = useState(false);
  const [showUpload, setShowUpload] = useState(false);
  const [analysingAOI, setAnalysingAOI] = useState(false);
  const [toastMessage, setToastMessage] = useState<string | null>(null);
  const analyseAOIMutation = useAnalyseAOI();
  // A pin for a searched place that has no boundary, and a sticky flag that retires the "select a
  // field" prompt once the analyst has navigated anywhere (so it never sits over a flown-to view).
  const [searchMarker, setSearchMarker] = useState<[number, number] | null>(null);
  const [hasNavigated, setHasNavigated] = useState(false);
  const flyToRef = useRef<((center: [number, number], zoom?: number) => void) | null>(null);
  const fitBoundsRef = useRef<((sw: [number, number], ne: [number, number]) => void) | null>(null);

  const selectedField = useMemo(
    () => fields.data?.find((f) => f.field_id === fieldId) ?? null,
    [fields.data, fieldId],
  );
  const list = useMemo(() => scenes.data ?? [], [scenes.data]);

  // A selected field or a drawn/entered AOI is a stronger target than a search pin, so retire the
  // pin (and the empty-state prompt) when either takes over.
  useEffect(() => {
    if (selectedField || customAOI) {
      setSearchMarker(null);
      setHasNavigated(true);
    }
  }, [selectedField, customAOI]);

  const activeSceneId = useMemo(() => {
    if (!list.length) return null;
    const match = passDate ? list.find((s) => s.pass_date === passDate) : undefined;
    return (match ?? list[list.length - 1]).scene_id;
  }, [list, passDate]);

  const comparing = compareDate !== null;
  const canCompare = useMemo(() => new Set(list.map((s) => s.pass_date)).size >= 2, [list]);

  const toggleCompare = () => {
    if (comparing) {
      setCompareDate(null);
      return;
    }
    const primary = passDate ?? (list.length ? list[list.length - 1].pass_date : null);
    const candidates = list.filter((s) => s.pass_date !== primary);
    if (candidates.length) setCompareDate(candidates[candidates.length - 1].pass_date);
  };

  // AOI tool handlers shared by the single map and the side-by-side comparison so search,
  // coordinate entry, and drawing behave the same in both modes.
  const handleDrawComplete = (polygon: import("geojson").Polygon) => {
    setCustomAOI(polygon);
    setDrawMode(false);
  };
  const handleDrawCancel = () => setDrawMode(false);

  const handleAnalyseAOI = () => {
    if (!customAOI) return;
    setAnalysingAOI(true);
    analyseAOIMutation.mutate(
      { geometry: customAOI, index },
      {
        onSuccess: (result) => {
          setAnalysingAOI(false);
          if (result.status === "no_scenes") {
            setToastMessage("No recent clear imagery for this area.");
          } else {
            const v = result.mean != null ? result.mean.toFixed(3) : "n/a";
            const clear =
              result.clear_fraction != null ? `${Math.round(result.clear_fraction * 100)}% clear` : "";
            const parts = [
              `${(result.index ?? index).toUpperCase()} ${v}`,
              clear,
              result.confidence,
              result.pass_date,
            ].filter(Boolean);
            setToastMessage(parts.join(" · "));
          }
          setTimeout(() => setToastMessage(null), 7000);
        },
        onError: (err) => {
          setAnalysingAOI(false);
          setToastMessage(`Failed: ${err instanceof Error ? err.message : String(err)}`);
          setTimeout(() => setToastMessage(null), 5000);
        },
      }
    );
  };
  const handleMapReady = (
    flyTo: (center: [number, number], zoom?: number) => void,
    fitBounds: (sw: [number, number], ne: [number, number]) => void,
  ) => {
    flyToRef.current = flyTo;
    fitBoundsRef.current = fitBounds;
  };

  return (
    <section className="relative min-h-0 h-full bg-bg">
      {/* Map — always mounted so the AOI toolbar and coordinate/geocoder callbacks have a
           live map regardless of field selection state. */}
      {comparing && selectedField ? (
        <SceneCompare
          field={selectedField}
          customAOI={customAOI}
          drawMode={drawMode}
          onDrawComplete={handleDrawComplete}
          onDrawCancel={handleDrawCancel}
          onMapReady={handleMapReady}
        />
      ) : (
        <SingleSceneMap
          field={selectedField}
          index={index}
          sceneId={activeSceneId}
          showRaster={showRaster}
          showRgb={showRgb}
          showFcc={showFcc}
          customAOI={customAOI}
          marker={searchMarker}
          drawMode={drawMode}
          onDrawComplete={handleDrawComplete}
          onDrawCancel={handleDrawCancel}
          onMapReady={handleMapReady}
        />
      )}

      {/* Empty-state overlay — shown over the map only until the analyst navigates anywhere
           (selecting a field, drawing/entering an AOI, or searching a place), so it never sits on
           top of a flown-to view. */}
      {!selectedField && !customAOI && !hasNavigated && (
        <div className="pointer-events-none absolute inset-0 grid place-items-center">
          <EmptyState
            title="Select a field"
            hint="Pick a farm and field to map its boundary and load its history."
          />
        </div>
      )}

      {/* AOI toolbar — sits at the very top of the map */}
      <div className="absolute inset-x-0 top-0 z-30 p-2">
        <AOIBar
          drawActive={drawMode}
          onDrawToggle={() => setDrawMode(true)}
          onCancelDraw={() => {
            setDrawMode(false);
          }}
          onOpenCoords={() => setShowCoords(true)}
          onOpenUpload={() => setShowUpload(true)}
          onAOISet={(geometry, label) => {
            // A boundary result supersedes the search pin; the field/AOI effect clears it, but do it
            // here too so a polygon pick never flashes a stray pin.
            setSearchMarker(null);
            setCustomAOI(geometry);
            setHasNavigated(true);
            // Note: geocoder results are not auto-saved — the analyst saves explicitly via the
            // CustomAOIPanel if they want to keep it.
            void label;
          }}
          onFlyTo={(center, zoom) => {
            flyToRef.current?.(center, zoom);
            // Drop a pin on the searched point so the fly-to has a visible target. A boundary result
            // calls onAOISet right after, which clears it again.
            setSearchMarker(center);
            setHasNavigated(true);
          }}
        />
      </div>

      {/* Left-side map controls — shifted down to clear the AOI bar */}
      <div className="absolute left-3 top-[72px] z-20 flex flex-col gap-2">
        <IconButton
          label={showRaster ? "Hide index layer" : "Show index layer"}
          active={showRaster}
          onClick={toggleRaster}
          disabled={!selectedField}
          className="border border-border bg-panel"
        >
          <Stack size={18} />
        </IconButton>
        <IconButton
          label={showRgb ? "Hide true color imagery" : "Show true color imagery"}
          active={showRgb}
          onClick={toggleRgb}
          disabled={!selectedField}
          className="border border-border bg-panel"
        >
          <Eye size={18} />
        </IconButton>
        <IconButton
          label={showFcc ? "Hide false color (NIR)" : "Show false color (NIR)"}
          active={showFcc}
          onClick={toggleFcc}
          disabled={!selectedField}
          className="border border-border bg-panel"
        >
          <Plant size={18} />
        </IconButton>
        <IconButton
          label={comparing ? "Exit comparison" : "Compare two passes"}
          active={comparing}
          onClick={toggleCompare}
          disabled={!selectedField || !canCompare}
          className="border border-border bg-panel"
        >
          <Columns size={18} />
        </IconButton>
      </div>

      {/* Clear AOI badge — centered below the toolbar */}
      {customAOI && !drawMode && (
        <div className="absolute inset-x-0 top-[72px] z-20 flex justify-center pointer-events-none gap-2">
          <button
            onClick={handleAnalyseAOI}
            disabled={analysingAOI}
            className="pointer-events-auto flex items-center gap-1.5 rounded-full bg-accent text-accent-fg border border-accent/20 px-4 py-1.5 text-xs font-medium hover:opacity-90 transition-opacity backdrop-blur-sm disabled:opacity-50"
          >
            <Lightning size={14} weight="fill" />
            {analysingAOI ? "Queueing..." : "Analyse this AOI"}
          </button>
          <button
            onClick={() => setCustomAOI(null)}
            className="pointer-events-auto flex items-center gap-1.5 rounded-full bg-panel/90 border border-border px-3 py-1 text-xs text-muted hover:text-fg transition-colors backdrop-blur-sm"
          >
            <X size={12} /> Clear custom AOI
          </button>
        </div>
      )}

      {/* Toast message overlay */}
      {toastMessage && (
        <div className="absolute bottom-4 right-4 z-50 bg-panel border border-border px-4 py-2.5 rounded-lg shadow-lg text-sm text-fg flex items-center gap-2 animate-in fade-in slide-in-from-bottom-2 duration-200">
          <Lightning size={16} className="text-accent animate-pulse" />
          <span>{toastMessage}</span>
          <button onClick={() => setToastMessage(null)} className="text-muted hover:text-fg ml-2 pointer-events-auto">
            <X size={14} />
          </button>
        </div>
      )}

      {/* Sidebar collapse toggle — left edge */}
      <button
        onClick={onToggleSidebar}
        aria-label={sidebarOpen ? "Collapse sidebar" : "Expand sidebar"}
        className="absolute left-0 top-1/2 -translate-y-1/2 z-30 flex h-12 w-5 items-center justify-center rounded-r-md bg-panel border border-l-0 border-border text-muted hover:text-fg transition-colors duration-150"
      >
        {sidebarOpen ? <CaretLeft size={12} weight="bold" /> : <CaretRight size={12} weight="bold" />}
      </button>

      {/* Inspector collapse toggle — right edge */}
      <button
        onClick={onToggleInspector}
        aria-label={inspectorOpen ? "Collapse inspector" : "Expand inspector"}
        className="absolute right-0 top-1/2 -translate-y-1/2 z-30 flex h-12 w-5 items-center justify-center rounded-l-md bg-panel border border-r-0 border-border text-muted hover:text-fg transition-colors duration-150"
      >
        {inspectorOpen ? <CaretRight size={12} weight="bold" /> : <CaretLeft size={12} weight="bold" />}
      </button>

      {/* Index legend — meaningless over the visual composites, so hidden with either */}
      {selectedField && !showRgb && !showFcc ? (
        <div className="absolute bottom-3 left-3 z-20">
          <IndexLegend meta={indexMeta(index)} />
        </div>
      ) : null}

      {/* Modals */}
      {showCoords && (
        <CoordinateEntryModal
          onClose={() => setShowCoords(false)}
          onApply={(geometry) => {
            setSearchMarker(null);
            setCustomAOI(geometry);
            setHasNavigated(true);
            const bbox = bboxOf(geometry);
            if (bbox) fitBoundsRef.current?.([bbox[0], bbox[1]], [bbox[2], bbox[3]]);
            setShowCoords(false);
          }}
          onFitBounds={(sw, ne) => {
            fitBoundsRef.current?.(sw, ne);
            setSearchMarker(null);
            setHasNavigated(true);
            setShowCoords(false);
          }}
        />
      )}
      {showUpload && (
        <FileUploadPanel
          onClose={() => setShowUpload(false)}
          onApply={(geometry, label) => {
            setSearchMarker(null);
            setCustomAOI(geometry);
            saveCustomAOI({ label, geometry });
            setHasNavigated(true);
            setShowUpload(false);
          }}
        />
      )}
    </section>
  );
}

function SingleSceneMap({
  field,
  index,
  sceneId,
  showRaster,
  showRgb,
  showFcc,
  customAOI,
  marker,
  drawMode,
  onDrawComplete,
  onDrawCancel,
  onMapReady,
}: {
  field: Field | null;
  index: IndexKey;
  sceneId: string | null;
  showRaster: boolean;
  showRgb: boolean;
  showFcc: boolean;
  customAOI: import("geojson").Geometry | null;
  marker: [number, number] | null;
  drawMode: boolean;
  onDrawComplete: (polygon: import("geojson").Polygon) => void;
  onDrawCancel: () => void;
  onMapReady: (
    flyTo: (center: [number, number], zoom?: number) => void,
    fitBounds: (sw: [number, number], ne: [number, number]) => void,
  ) => void;
}) {
  const ref = useRef<HTMLDivElement | null>(null);
  useFieldMap(ref, {
    field,
    index,
    sceneId,
    showRaster,
    showRgb,
    showFcc,
    customAOI,
    marker,
    drawMode,
    onDrawComplete,
    onDrawCancel,
    onMapReady,
  });
  // size-full (not absolute inset-0): MapLibre's stylesheet sets `.maplibregl-map { position:
  // relative }` unlayered, which beats Tailwind v4's layered `absolute` utility — so inset-0 would
  // collapse the container to 0 height. An explicit 100% width/height is immune to that.
  return <div ref={ref} className="size-full" />;
}
