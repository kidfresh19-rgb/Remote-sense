import { ChartBar } from "@phosphor-icons/react";
import { useState } from "react";

import { EmptyState, ErrorState, LoadingRows } from "@/components/states";
import { SegmentedControl } from "@/components/ui";
import type { RollupNode } from "@/lib/api";
import { cn, formatPercent } from "@/lib/format";
import { useWardWatchRollups } from "@/lib/queries";

type Level = "ward" | "district" | "province";

const LEVEL_OPTIONS: { value: Level; label: string }[] = [
  { value: "ward", label: "Ward" },
  { value: "district", label: "District" },
  { value: "province", label: "Province" },
];

// The four movement categories as a stacked bar. Systemic (the food-security alarm) leads, then
// idiosyncratic; the two "doing fine" categories trail. Tones match lib/wardwatch.movementMeta.
const SEGMENTS: { key: "systemic" | "idiosyncratic" | "resilient" | "nominal"; cls: string; label: string }[] = [
  { key: "systemic", cls: "bg-caution", label: "Systemic" },
  { key: "idiosyncratic", cls: "bg-critical", label: "Idiosyncratic" },
  { key: "resilient", cls: "bg-positive", label: "Resilient" },
  { key: "nominal", cls: "bg-panel-2", label: "Nominal" },
];

function RollupRow({ node }: { node: RollupNode }) {
  const tone =
    node.systemic_fraction >= 0.3
      ? "text-critical"
      : node.systemic_fraction > 0
        ? "text-caution"
        : "text-muted";
  return (
    <li className="px-4 py-3">
      <div className="flex items-baseline justify-between gap-3">
        <p className="truncate text-sm font-medium text-fg">{node.name}</p>
        <div className="flex shrink-0 items-baseline gap-2">
          <span className={cn("tnum text-sm font-semibold", tone)}>
            {formatPercent(node.systemic_fraction)}
          </span>
          <span className="text-[10px] uppercase tracking-wide text-muted/70">systemic</span>
        </div>
      </div>
      <div className="mt-2 flex h-2 overflow-hidden rounded-full bg-panel-2">
        {node.total > 0
          ? SEGMENTS.map((seg) => {
              const count = node[seg.key];
              if (count <= 0) return null;
              return (
                <div
                  key={seg.key}
                  className={seg.cls}
                  style={{ width: `${(count / node.total) * 100}%` }}
                  title={`${seg.label}: ${count}`}
                />
              );
            })
          : null}
      </div>
      <p className="mt-1.5 text-xs text-muted">
        {node.total} household{node.total === 1 ? "" : "s"} · {node.distressed} distressed
      </p>
    </li>
  );
}

/** Food-security rollups (PRD 0003 §10): the same households the queue ranks, tallied per ward,
 *  district and province with systemic and idiosyncratic kept separate. District/province carry a
 *  single "unassigned" node until ward-boundary procurement lands. */
export function FoodSecurityRollups() {
  const rollups = useWardWatchRollups();
  const [level, setLevel] = useState<Level>("ward");

  const nodes = rollups.data
    ? level === "ward"
      ? rollups.data.by_ward
      : level === "district"
        ? rollups.data.by_district
        : rollups.data.by_province
    : [];

  return (
    <div className="flex h-full flex-col">
      <div className="flex items-center justify-between gap-3 border-b border-border px-4 py-3">
        <div>
          <h2 className="text-sm font-semibold text-fg">Food security</h2>
          <p className="text-xs text-muted">Systemic vs idiosyncratic, worst first</p>
        </div>
        <SegmentedControl
          options={LEVEL_OPTIONS}
          value={level}
          onChange={setLevel}
          ariaLabel="Rollup level"
        />
      </div>
      <div className="flex-1 overflow-y-auto">
        {rollups.isPending ? (
          <LoadingRows rows={6} />
        ) : rollups.isError ? (
          <ErrorState error={rollups.error} onRetry={() => rollups.refetch()} />
        ) : nodes.length === 0 ? (
          <EmptyState
            icon={<ChartBar size={22} />}
            title="No rollup yet"
            hint={
              level === "ward"
                ? "No household has an assessable plot series in any ward yet."
                : "District and province roll-ups light up once ward-boundary procurement lands; today they report a single unassigned node."
            }
          />
        ) : (
          <ul className="divide-y divide-border">
            {nodes.map((node) => (
              <RollupRow key={node.name} node={node} />
            ))}
          </ul>
        )}
      </div>
    </div>
  );
}
