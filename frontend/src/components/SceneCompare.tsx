import type { Geometry, Polygon } from "geojson";
import type { Map as MaplibreMap } from "maplibre-gl";
import { useEffect, useMemo, useRef, useState, type ReactNode } from "react";

import type { Field } from "@/lib/api";
import { formatDate } from "@/lib/format";
import { indexMeta, type IndexKey } from "@/lib/indices";
import { syncCameras } from "@/lib/mapSync";
import { useScenes } from "@/lib/queries";
import { useWorkspace } from "@/state/workspace";

import { DiffLegend } from "./DiffLegend";
import { SegmentedControl } from "./ui";
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

/** Comparison of two passes of the same field: either side-by-side on a shared (synced) camera -
 *  the left pane tracks the primary pass (the timeline selection), the right pane the compare pass
 *  - or, once both are explicitly picked, a single pass-to-pass difference layer (backlog 0045).
 *  Toggled by `compareMode`; switching between the two never re-picks A or B. */
export function SceneCompare({
  field,
  customAOI,
  drawMode,
  onDrawComplete,
  onDrawCancel,
  onMapReady,
}: SceneCompareProps) {
  const {
    index,
    showRaster,
    showRgb,
    showCloudMask,
    passDate,
    compareDate,
    setPassDate,
    setCompareDate,
    compareMode,
    setCompareMode,
  } = useWorkspace();
  const scenes = useScenes(field.field_id);
  const list = useMemo(() => scenes.data ?? [], [scenes.data]);
  const dates = useMemo(() => list.map((s) => s.pass_date), [list]);

  const leftScene = useMemo(() => {
    if (!list.length) return null;
    return (passDate ? list.find((s) => s.pass_date === passDate) : undefined) ?? list[list.length - 1];
  }, [list, passDate]);

  const rightScene = useMemo(
    () => (compareDate ? (list.find((s) => s.pass_date === compareDate) ?? null) : null),
    [list, compareDate],
  );

  // The difference view (backlog 0045) never guesses a pair: it requires an explicit pick on
  // *both* sides, not merely a resolved scene on both. leftScene falls back to the latest pass
  // when passDate is unset - the right default for side-by-side, since some initial view beats a
  // blank pane - but silently feeding that guess into a diff would break invariant 4's posture
  // (never fabricate a pass; show explicit absence instead of a guess).
  const explicitPair = passDate !== null && compareDate !== null;
  const diffMode = compareMode === "diff" && explicitPair;

  const [leftMap, setLeftMap] = useState<MaplibreMap | null>(null);
  const [rightMap, setRightMap] = useState<MaplibreMap | null>(null);

  useEffect(() => {
    if (!leftMap || !rightMap) return;
    return syncCameras([leftMap, rightMap]);
  }, [leftMap, rightMap]);

  const modeToggle = (
    <div className="absolute right-3 top-3 z-10">
      <SegmentedControl<"side-by-side" | "diff">
        ariaLabel="Comparison view"
        value={compareMode}
        onChange={setCompareMode}
        options={[
          { value: "side-by-side", label: "Side by side" },
          {
            value: "diff",
            label: "Diff",
            disabled: !explicitPair,
            title: explicitPair
              ? "Show B minus A as a difference layer"
              : "Pick both A and B passes to see the difference",
          },
        ]}
      />
    </div>
  );

  if (diffMode && leftScene && rightScene) {
    return (
      <div className="absolute inset-0">
        <CompareCell
          field={field}
          index={index}
          sceneId={null}
          showRaster={false}
          showRgb={false}
          showCloudMask={false}
          diffPass={{ sceneA: leftScene.scene_id, sceneB: rightScene.scene_id }}
          fit
          controls
          onMap={setLeftMap}
          customAOI={customAOI}
          drawMode={drawMode}
          onDrawComplete={onDrawComplete}
          onDrawCancel={onDrawCancel}
          onMapReady={onMapReady}
        >
          <PassPickerPair
            dates={dates}
            valueA={leftScene.pass_date}
            valueB={rightScene.pass_date}
            onChangeA={setPassDate}
            onChangeB={setCompareDate}
          />
          <div className="absolute bottom-3 left-3 z-10">
            <DiffLegend meta={indexMeta(index)} dateA={leftScene.pass_date} dateB={rightScene.pass_date} />
          </div>
        </CompareCell>
        {modeToggle}
      </div>
    );
  }

  return (
    <div className="absolute inset-0 grid grid-rows-2 gap-px bg-border lg:grid-cols-2 lg:grid-rows-1">
      <CompareCell
        field={field}
        index={index}
        sceneId={leftScene?.scene_id ?? null}
        showRaster={showRaster}
        showRgb={showRgb}
        showCloudMask={showCloudMask}
        fit
        controls
        onMap={setLeftMap}
        customAOI={customAOI}
        drawMode={drawMode}
        onDrawComplete={onDrawComplete}
        onDrawCancel={onDrawCancel}
        onMapReady={onMapReady}
      >
        <PassPicker
          side="A"
          dates={dates}
          value={leftScene?.pass_date ?? null}
          onChange={setPassDate}
        />
      </CompareCell>

      <CompareCell
        field={field}
        index={index}
        sceneId={rightScene?.scene_id ?? null}
        showRaster={showRaster}
        showRgb={showRgb}
        showCloudMask={showCloudMask}
        fit={false}
        controls={false}
        onMap={setRightMap}
        customAOI={customAOI}
      >
        <PassPicker
          side="B"
          dates={dates}
          value={compareDate}
          onChange={setCompareDate}
        />
      </CompareCell>
      {modeToggle}
    </div>
  );
}

function CompareCell({
  field,
  index,
  sceneId,
  showRaster,
  showRgb,
  showCloudMask,
  diffPass,
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
  showRaster: boolean;
  showRgb: boolean;
  showCloudMask: boolean;
  /** Pass-to-pass difference layer (backlog 0045) - see useFieldMap's diffPass. */
  diffPass?: { sceneA: string; sceneB: string } | null;
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
    showRaster,
    showRgb,
    showCloudMask,
    diffPass,
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

function PassPicker({
  side,
  dates,
  value,
  onChange,
}: {
  side: "A" | "B";
  dates: string[];
  value: string | null;
  onChange: (date: string) => void;
}) {
  return (
    <div className="absolute left-1/2 top-3 z-10 -translate-x-1/2">
      <PassPickerControl side={side} dates={dates} value={value} onChange={onChange} />
    </div>
  );
}

/** Both A and B pickers together, for the single-pane diff view (backlog 0045) - the same two
 *  controls the side-by-side panes use, just sharing one positioned wrapper instead of one each,
 *  so switching modes never re-picks or repositions them relative to each other. */
function PassPickerPair({
  dates,
  valueA,
  valueB,
  onChangeA,
  onChangeB,
}: {
  dates: string[];
  valueA: string | null;
  valueB: string | null;
  onChangeA: (date: string) => void;
  onChangeB: (date: string) => void;
}) {
  return (
    <div className="absolute left-1/2 top-3 z-10 flex -translate-x-1/2 items-center gap-2">
      <PassPickerControl side="A" dates={dates} value={valueA} onChange={onChangeA} />
      <PassPickerControl side="B" dates={dates} value={valueB} onChange={onChangeB} />
    </div>
  );
}

function PassPickerControl({
  side,
  dates,
  value,
  onChange,
}: {
  side: "A" | "B";
  dates: string[];
  value: string | null;
  onChange: (date: string) => void;
}) {
  return (
    <label className="flex items-center gap-2 rounded-md border border-border bg-panel/90 px-2 py-1 text-xs shadow-sm backdrop-blur">
      <span className="font-semibold text-accent">{side}</span>
      <select
        aria-label={`Pass ${side}`}
        value={value ?? ""}
        onChange={(e) => onChange(e.target.value)}
        className="bg-transparent text-fg focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-accent"
      >
        {value === null ? <option value="">Select a pass</option> : null}
        {dates.map((d) => (
          <option key={d} value={d}>
            {formatDate(d)}
          </option>
        ))}
      </select>
    </label>
  );
}
