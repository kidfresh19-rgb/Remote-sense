import { diffGradientCss, diffRange, type IndexMeta } from "@/lib/indices";
import { formatDate, formatNumber } from "@/lib/format";

/** Legend for the pass-to-pass difference layer (backlog 0045): states the comparison direction
 *  and both dates explicitly (B minus A is otherwise ambiguous - the sign of a color must never be
 *  left for the analyst to guess), plus the symmetric range the diverging ramp was clamped to. */
export function DiffLegend({
  meta,
  dateA,
  dateB,
}: {
  meta: IndexMeta;
  dateA: string;
  dateB: string;
}) {
  const [lo, hi] = diffRange(meta);
  return (
    <div className="rounded-lg border border-border bg-panel p-2.5 shadow-sm">
      <div className="mb-1 flex items-baseline justify-between gap-6">
        <span className="text-xs font-semibold text-fg">{meta.label} difference</span>
        <span className="text-[10px] text-muted">
          B minus A · {formatDate(dateB)} minus {formatDate(dateA)}
        </span>
      </div>
      <div className="h-2 w-44 rounded" style={{ backgroundImage: diffGradientCss() }} />
      <div className="mt-1 flex w-44 justify-between text-[10px] text-muted tnum">
        <span>{formatNumber(lo, 1)}</span>
        <span>0</span>
        <span>{formatNumber(hi, 1)}</span>
      </div>
    </div>
  );
}
