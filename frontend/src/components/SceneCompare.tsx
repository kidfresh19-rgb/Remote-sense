import type { Geometry, Polygon } from "geojson";
import type { Map as MaplibreMap } from "maplibre-gl";
import { useEffect, useMemo, useRef, useState, type ReactNode } from "react";

import type { Field, TimeseriesPoint } from "@/lib/api";
import { indexMeta, type IndexKey } from "@/lib/indices";
import type { MapView } from "@/lib/mapView";
import { syncCameras } from "@/lib/mapSync";
import { useScenes, useTimeseries } from "@/lib/queries";
import { useWorkspace } from "@/state/workspace";

import { MapPassReadout } from "./MapPassReadout";
import { useFieldMap } from "./useFieldMap";

interface SceneCompareProps {
  field: Field;
  /** Custom AOI overlay, drawn on both panes for visual parity with the single map. */
  customAOI?: Geometry | null;
  /** Draw mode and its callbacks are wired to the primary (left) pane only. */
  drawMode?: boolean;
  onDrawComplete?: (polygon: Polygon) => void;
  onDrawCancel?: () => void;
  onMapReady?: (
    flyTo: (center: [number, number], zoom?: number) => void,
    fitBounds: (sw: [number, number], ne: [number, number]) => void,
  ) => void;
}

/** Side-by-side comparison of two passes of the same field, on a shared (synced) camera. The left
 *  pane tracks the primary pass (the timeline selection), the right pane the compare pass. */
export function SceneCompare({
  field,
  customAOI,
  drawMode,
  onDrawComplete,
  onDrawCancel,
  onMapReady,
}: SceneCompareProps) {
  const { index, mapView, passDate, compareDate, setPassDate, setCompareDate } = useWorkspace();
  const scenes = useScenes(field.field_id);
  const list = useMemo(() => scenes.data ?? [], [scenes.data]);
  // Unique pass dates, oldest first (a field can hold several scenes on one date from adjacent MGRS
  // tiles). Dedupe so the pane pickers key and step on distinct passes; Set preserves list order.
  const dates = useMemo(() => Array.from(new Set(list.map((s) => s.pass_date))), [list]);

  // Per-pass numbers so each pane shows its mean index, clear fraction and confidence inline. Shares
  // the ["timeseries", fieldId, index] cache key with the Series tab, so this adds no extra fetch.
  const meta = indexMeta(index);
  const timeseries = useTimeseries(field.field_id, index);
  const pointByDate = useMemo(() => {
    const m = new Map<string, TimeseriesPoint>();
    for (const p of timeseries.data ?? []) m.set(p.pass_date, p);
    return m;
  }, [timeseries.data]);
  const clearByDate = useMemo(() => {
    const m = new Map<string, number>();
    for (const s of list) m.set(s.pass_date, s.clear_fraction);
    return m;
  }, [list]);

  const leftScene = useMemo(() => {
    if (!list.length) return null;
    return (passDate ? list.find((s) => s.pass_date === passDate) : undefined) ?? list[list.length - 1];
  }, [list, passDate]);

  const rightScene = useMemo(
    () => (compareDate ? (list.find((s) => s.pass_date === compareDate) ?? null) : null),
    [list, compareDate],
  );

  const [leftMap, setLeftMap] = useState<MaplibreMap | null>(null);
  const [rightMap, setRightMap] = useState<MaplibreMap | null>(null);

  useEffect(() => {
    if (!leftMap || !rightMap) return;
    return syncCameras([leftMap, rightMap]);
  }, [leftMap, rightMap]);

  return (
    <div className="absolute inset-0 grid grid-rows-2 gap-px bg-border lg:grid-cols-2 lg:grid-rows-1">
      <CompareCell
        field={field}
        index={index}
        sceneId={leftScene?.scene_id ?? null}
        mapView={mapView}
        fit
        controls
        onMap={setLeftMap}
        customAOI={customAOI}
        drawMode={drawMode}
        onDrawComplete={onDrawComplete}
        onDrawCancel={onDrawCancel}
        onMapReady={onMapReady}
      >
        <div className="pointer-events-none absolute inset-x-0 top-16 z-10 flex justify-center px-3">
          <MapPassReadout
            side="A"
            meta={meta}
            dates={dates}
            value={leftScene?.pass_date ?? null}
            onChange={setPassDate}
            points={pointByDate}
            clearByDate={clearByDate}
          />
        </div>
      </CompareCell>

      <CompareCell
        field={field}
        index={index}
        sceneId={rightScene?.scene_id ?? null}
        mapView={mapView}
        fit={false}
        controls={false}
        onMap={setRightMap}
        customAOI={customAOI}
      >
        <div className="pointer-events-none absolute inset-x-0 top-16 z-10 flex justify-center px-3">
          <MapPassReadout
            side="B"
            meta={meta}
            dates={dates}
            value={compareDate}
            onChange={setCompareDate}
            points={pointByDate}
            clearByDate={clearByDate}
          />
        </div>
      </CompareCell>
    </div>
  );
}

function CompareCell({
  field,
  index,
  sceneId,
  mapView,
  fit,
  controls,
  onMap,
  customAOI,
  drawMode,
  onDrawComplete,
  onDrawCancel,
  onMapReady,
  children,
}: {
  field: Field;
  index: IndexKey;
  sceneId: string | null;
  mapView: MapView;
  fit: boolean;
  controls: boolean;
  onMap: (map: MaplibreMap | null) => void;
  customAOI?: Geometry | null;
  drawMode?: boolean;
  onDrawComplete?: (polygon: Polygon) => void;
  onDrawCancel?: () => void;
  onMapReady?: (
    flyTo: (center: [number, number], zoom?: number) => void,
    fitBounds: (sw: [number, number], ne: [number, number]) => void,
  ) => void;
  children: ReactNode;
}) {
  const ref = useRef<HTMLDivElement | null>(null);
  useFieldMap(ref, {
    field,
    index,
    sceneId,
    mapView,
    fit,
    controls,
    onMap,
    customAOI,
    drawMode,
    onDrawComplete,
    onDrawCancel,
    onMapReady,
  });
  return (
    <div className="relative min-h-0">
      {/* size-full, not absolute inset-0: MapLibre sets `.maplibregl-map { position: relative }`
          unlayered, overriding Tailwind v4's layered `absolute` and collapsing the map to 0 height. */}
      <div ref={ref} className="size-full" />
      {children}
    </div>
  );
}
