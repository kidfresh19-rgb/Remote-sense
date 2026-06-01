import { useMemo } from "react";

import { dateValue, formatDate, formatDateShort, formatNumber, formatPercent } from "@/lib/format";
import { colorForValue, indexMeta } from "@/lib/indices";
import { useTimeseries } from "@/lib/queries";
import { useWorkspace } from "@/state/workspace";

import { EmptyState, ErrorState, LoadingRows } from "./states";

const W = 320;
const H = 150;
const PAD = { l: 30, r: 10, t: 12, b: 22 };

export function IndexTimeseriesChart({ fieldId }: { fieldId: string }) {
  const { index, passDate, setPassDate } = useWorkspace();
  const meta = indexMeta(index);
  const query = useTimeseries(fieldId, index);

  const points = useMemo(
    () => (query.data ?? []).filter((p) => p.mean !== null),
    [query.data],
  );

  if (query.isLoading) return <LoadingRows rows={4} />;
  if (query.isError) return <ErrorState error={query.error} onRetry={() => query.refetch()} />;
  if (!points.length) {
    return (
      <EmptyState
        title={`No ${meta.label} history`}
        hint="No usable passes yet for this field at the current geometry version."
      />
    );
  }

  const xs = points.map((p) => dateValue(p.pass_date));
  const minX = Math.min(...xs);
  const maxX = Math.max(...xs);
  const spanX = maxX - minX || 1;
  const lo = Math.min(meta.min, ...points.map((p) => p.p10 ?? p.mean ?? meta.min));
  const hi = Math.max(meta.max, ...points.map((p) => p.p90 ?? p.mean ?? meta.max));
  const spanY = hi - lo || 1;
  const sx = (v: number) => PAD.l + ((v - minX) / spanX) * (W - PAD.l - PAD.r);
  const sy = (v: number) => PAD.t + (1 - (v - lo) / spanY) * (H - PAD.t - PAD.b);

  const meanPath = points
    .map((p, i) => `${i === 0 ? "M" : "L"} ${sx(xs[i]).toFixed(1)} ${sy(p.mean!).toFixed(1)}`)
    .join(" ");

  const band: string[] = [];
  for (let i = 0; i < points.length; i++) {
    band.push(`${sx(xs[i]).toFixed(1)},${sy(points[i].p90 ?? points[i].mean!).toFixed(1)}`);
  }
  for (let i = points.length - 1; i >= 0; i--) {
    band.push(`${sx(xs[i]).toFixed(1)},${sy(points[i].p10 ?? points[i].mean!).toFixed(1)}`);
  }
  const bandPath = `M ${band.join(" L ")} Z`;

  const active = points.find((p) => p.pass_date === passDate) ?? points[points.length - 1];
  const yTicks = [lo, (lo + hi) / 2, hi];

  return (
    <div className="p-3">
      <svg
        viewBox={`0 0 ${W} ${H}`}
        className="w-full"
        role="img"
        aria-label={`${meta.long} time series, ${points.length} passes`}
      >
        {yTicks.map((v, i) => (
          <g key={i}>
            <line
              x1={PAD.l}
              x2={W - PAD.r}
              y1={sy(v)}
              y2={sy(v)}
              stroke="var(--border)"
              strokeWidth={1}
            />
            <text
              x={PAD.l - 4}
              y={sy(v) + 3}
              textAnchor="end"
              fontSize={8}
              className="fill-[var(--muted)] tnum"
            >
              {v.toFixed(1)}
            </text>
          </g>
        ))}

        <path d={bandPath} fill="var(--accent)" fillOpacity={0.12} />
        <path
          d={meanPath}
          fill="none"
          stroke="var(--accent)"
          strokeWidth={1.5}
          strokeLinejoin="round"
          strokeLinecap="round"
        />

        {points.map((p, i) => {
          const isActive = p.pass_date === active.pass_date;
          return (
            <g
              key={p.pass_date}
              className="cursor-pointer"
              onClick={() => setPassDate(p.pass_date)}
            >
              {isActive ? (
                <circle
                  cx={sx(xs[i])}
                  cy={sy(p.mean!)}
                  r={7}
                  fill="none"
                  stroke="var(--accent)"
                  strokeWidth={1}
                />
              ) : null}
              <circle
                cx={sx(xs[i])}
                cy={sy(p.mean!)}
                r={isActive ? 4 : 2.5}
                fill={colorForValue(meta, p.mean!)}
                stroke="var(--panel)"
                strokeWidth={isActive ? 1.5 : 1}
                opacity={0.4 + 0.6 * p.clear_fraction}
              />
            </g>
          );
        })}

        <text x={PAD.l} y={H - 6} textAnchor="start" fontSize={8} className="fill-[var(--muted)]">
          {formatDateShort(points[0].pass_date)}
        </text>
        <text
          x={W - PAD.r}
          y={H - 6}
          textAnchor="end"
          fontSize={8}
          className="fill-[var(--muted)]"
        >
          {formatDateShort(points[points.length - 1].pass_date)}
        </text>
      </svg>

      <dl className="mt-3 grid grid-cols-3 gap-2">
        <Stat label={`Mean ${meta.label}`} value={formatNumber(active.mean)} />
        <Stat label="Clear" value={formatPercent(active.clear_fraction)} />
        <Stat label="Confidence" value={active.confidence ?? "·"} />
      </dl>
      <p className="mt-2 text-xs text-muted tnum">
        {formatDate(active.pass_date)} · p10 {formatNumber(active.p10)} · p90{" "}
        {formatNumber(active.p90)}
      </p>
    </div>
  );
}

function Stat({ label, value }: { label: string; value: string }) {
  return (
    <div className="rounded-md bg-panel-2 px-2 py-1.5">
      <dt className="truncate text-[10px] uppercase tracking-wide text-muted">{label}</dt>
      <dd className="text-sm font-medium text-fg tnum">{value}</dd>
    </div>
  );
}
