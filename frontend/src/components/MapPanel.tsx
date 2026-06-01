import { Columns, Stack } from "@phosphor-icons/react";
import { useMemo, useRef } from "react";

import type { Field } from "@/lib/api";
import { indexMeta, type IndexKey } from "@/lib/indices";
import { useFields, useScenes } from "@/lib/queries";
import { useWorkspace } from "@/state/workspace";

import { IndexLegend } from "./IndexLegend";
import { SceneCompare } from "./SceneCompare";
import { EmptyState } from "./states";
import { IconButton } from "./ui";
import { useFieldMap } from "./useFieldMap";

export function MapPanel() {
  const { farmId, fieldId, index, passDate, compareDate, showRaster, toggleRaster, setCompareDate } =
    useWorkspace();
  const fields = useFields(farmId);
  const scenes = useScenes(fieldId);

  const selectedField = useMemo(
    () => fields.data?.find((f) => f.field_id === fieldId) ?? null,
    [fields.data, fieldId],
  );
  const list = useMemo(() => scenes.data ?? [], [scenes.data]);

  // The pass the single-map overlay shows: the scrubbed pass, else the most recent.
  const activeSceneId = useMemo(() => {
    if (!list.length) return null;
    const match = passDate ? list.find((s) => s.pass_date === passDate) : undefined;
    return (match ?? list[list.length - 1]).scene_id;
  }, [list, passDate]);

  const comparing = compareDate !== null;
  // Two passes are comparable only if there are two distinct dates: the /scenes route can return
  // several scene ids on one date, which would otherwise enable the toggle but leave it inert.
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

  return (
    <section className="relative min-h-[55vh] bg-bg lg:min-h-0">
      {selectedField ? (
        comparing ? (
          <SceneCompare field={selectedField} />
        ) : (
          <SingleSceneMap
            field={selectedField}
            index={index}
            sceneId={activeSceneId}
            showRaster={showRaster}
          />
        )
      ) : (
        <div className="pointer-events-none absolute inset-0 grid place-items-center">
          <EmptyState
            title="Select a field"
            hint="Pick a farm and field to map its boundary and load its history."
          />
        </div>
      )}

      <div className="absolute left-3 top-3 z-20 flex flex-col gap-2">
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
          label={comparing ? "Exit comparison" : "Compare two passes"}
          active={comparing}
          onClick={toggleCompare}
          disabled={!selectedField || !canCompare}
          className="border border-border bg-panel"
        >
          <Columns size={18} />
        </IconButton>
      </div>

      {selectedField ? (
        <div className="absolute bottom-3 left-3 z-20">
          <IndexLegend meta={indexMeta(index)} />
        </div>
      ) : null}
    </section>
  );
}

function SingleSceneMap({
  field,
  index,
  sceneId,
  showRaster,
}: {
  field: Field;
  index: IndexKey;
  sceneId: string | null;
  showRaster: boolean;
}) {
  const ref = useRef<HTMLDivElement | null>(null);
  useFieldMap(ref, { field, index, sceneId, showRaster });
  return <div ref={ref} className="absolute inset-0" />;
}
