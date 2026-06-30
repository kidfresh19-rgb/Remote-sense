import type { VisitTrendPoint } from "@/lib/api";
import { formatDate, formatNumber } from "@/lib/format";

const W = 240;
const H = 48;

/** A glanceable NDVI sparkline for one plot's stored passes. Low-pixel-quality passes are kept and
 *  marked (caution dots), never dropped (§4 honesty). Under two clear passes there is no trend to
 *  draw, which the cockpit states plainly rather than faking a line. */
export function PlotTrend({ trend }: { trend: VisitTrendPoint[] }) {
  const points = trend.filter(
    (p): p is VisitTrendPoint & { ndvi_mean: number } => p.ndvi_mean !== null,
  );
  if (points.length < 2) {
    return <p className="text-xs text-muted">Not enough clear passes to chart a trend.</p>;
  }
  const ys = points.map((p) => p.ndvi_mean);
  const lo = Math.min(...ys);
  const hi = Math.max(...ys);
  const spanY = hi - lo || 1;
  const sx = (i: number) => 4 + (i / (points.length - 1)) * (W - 8);
  const sy = (v: number) => H - 6 - ((v - lo) / spanY) * (H - 12);
  const path = points
    .map((p, i) => `${i === 0 ? "M" : "L"} ${sx(i).toFixed(1)} ${sy(p.ndvi_mean).toFixed(1)}`)
    .join(" ");
  const last = points[points.length - 1];
  return (
    <div>
      <svg viewBox={`0 0 ${W} ${H}`} className="h-12 w-full" role="img" aria-label="NDVI trend">
        <path
          d={path}
          fill="none"
          stroke="var(--accent)"
          strokeWidth={1.5}
          strokeLinejoin="round"
          strokeLinecap="round"
        />
        {points.map((p, i) => (
          <circle
            key={p.pass_date}
            cx={sx(i)}
            cy={sy(p.ndvi_mean)}
            r={p.low_pixel_quality ? 2.5 : 2}
            fill={p.low_pixel_quality ? "var(--caution)" : "var(--accent)"}
          >
            <title>
              {formatDate(p.pass_date)}: {formatNumber(p.ndvi_mean, 2)} NDVI
              {p.low_pixel_quality ? " (low pixel quality)" : ""}
            </title>
          </circle>
        ))}
      </svg>
      <p className="mt-1 flex items-center justify-between text-[11px] text-muted">
        <span>
          {points.length} clear pass{points.length === 1 ? "" : "es"}
        </span>
        <span>
          latest {formatNumber(last.ndvi_mean, 2)} NDVI · {formatDate(last.pass_date)}
        </span>
      </p>
    </div>
  );
}
