import type { Map as MaplibreMap } from "maplibre-gl";
import { useEffect, useMemo, useRef, useState, type ReactNode } from "react";

import type { Field } from "@/lib/api";
import { formatDate } from "@/lib/format";
import type { IndexKey } from "@/lib/indices";
import { syncCameras } from "@/lib/mapSync";
import { useScenes } from "@/lib/queries";
import { useWorkspace } from "@/state/workspace";

import { useFieldMap } from "./useFieldMap";

/** Side-by-side comparison of two passes of the same field, on a shared (synced) camera. The left
 *  pane tracks the primary pass (the timeline selection), the right pane the compare pass. */
export function SceneCompare({ field }: { field: Field }) {
  const { index, showRaster, passDate, compareDate, setPassDate, setCompareDate } = useWorkspace();
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
        showRaster={showRaster}
        fit
        controls
        onMap={setLeftMap}
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
        fit={false}
        controls={false}
        onMap={setRightMap}
      >
        <PassPicker
          side="B"
          dates={dates}
          value={compareDate}
          onChange={setCompareDate}
        />
      </CompareCell>
    </div>
  );
}

function CompareCell({
  field,
  index,
  sceneId,
  showRaster,
  fit,
  controls,
  onMap,
  children,
}: {
  field: Field;
  index: IndexKey;
  sceneId: string | null;
  showRaster: boolean;
  fit: boolean;
  controls: boolean;
  onMap: (map: MaplibreMap | null) => void;
  children: ReactNode;
}) {
  const ref = useRef<HTMLDivElement | null>(null);
  useFieldMap(ref, { field, index, sceneId, showRaster, fit, controls, onMap });
  return (
    <div className="relative min-h-0">
      <div ref={ref} className="absolute inset-0" />
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
    <label className="absolute left-1/2 top-3 z-10 flex -translate-x-1/2 items-center gap-2 rounded-md border border-border bg-panel/90 px-2 py-1 text-xs shadow-sm backdrop-blur">
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
