import { gradientCss, type IndexMeta } from "@/lib/indices";
import { formatNumber } from "@/lib/format";

export function IndexLegend({ meta }: { meta: IndexMeta }) {
  return (
    <div className="rounded-lg border border-border bg-panel p-2.5 shadow-sm">
      <div className="mb-1 flex items-baseline justify-between gap-6">
        <span className="text-xs font-semibold text-fg">{meta.label}</span>
        <span className="text-[10px] text-muted">{meta.description}</span>
      </div>
      <div className="h-2 w-44 rounded" style={{ backgroundImage: gradientCss(meta) }} />
      <div className="mt-1 flex w-44 justify-between text-[10px] text-muted tnum">
        <span>{formatNumber(meta.min, 1)}</span>
        <span>{formatNumber(meta.max, 1)}</span>
      </div>
    </div>
  );
}
