import { DownloadSimple, Spinner } from "@phosphor-icons/react";
import { useMemo } from "react";

import type { AOIJob, AOISeriesPass } from "@/lib/api";
import { cn, dateValue, formatDate, formatNumber, formatPercent } from "@/lib/format";
import { colorForValue, indexMeta, INDICES, type IndexKey } from "@/lib/indices";

import { EmptyState } from "../states";
import { Badge } from "../ui";

export type SelectedIndex = IndexKey | "all";

interface AOIResultsTableProps {
  jobs: Record<IndexKey, AOIJob | undefined>;
  pending: boolean;
  selectedIndex: SelectedIndex;
  viewIndex: IndexKey;
  onViewIndexChange: (idx: IndexKey) => void;
}

export function AOIResultsTable({
  jobs,
  pending,
  selectedIndex,
  viewIndex,
  onViewIndexChange,
}: AOIResultsTableProps) {
  const meta = indexMeta(viewIndex);
  const hasAnyJob = Object.values(jobs).some((j) => !!j);

  if (!hasAnyJob && !pending) {
    return (
      <EmptyState
        title="No analysis yet"
        hint="Pick an area, choose an index, then run a batch of dates or a backfill sweep. Results appear here."
      />
    );
  }

  function renderJobContent() {
    const job = jobs[viewIndex];

    if (pending && !job) {
      return <RunningState job={undefined} />;
    }

    if (job?.state === "queued" || job?.state === "running") {
      return <RunningState job={job} />;
    }

    if (job?.state === "error") {
      return (
        <div className="p-4">
          <p className="text-sm font-medium text-critical">Analysis failed ({meta.label})</p>
          <p className="mt-1 max-w-[60ch] text-xs leading-relaxed text-muted">
            {job.error ?? "The imagery service could not complete this run. Try a smaller area or range."}
          </p>
        </div>
      );
    }

    if (!job) {
      return (
        <div className="flex h-full items-center justify-center p-8 text-center text-muted">
          <p className="text-sm">Ready to analyse {meta.label}</p>
        </div>
      );
    }

    const result = job.result ?? null;
    const passes = result?.passes ?? [];
    const hasRequested = passes.some((p) => p.requested_date);

    if (!result || passes.length === 0) {
      return (
        <EmptyState
          title={`No passes found for ${meta.label}`}
          hint="No usable imagery matched this area and range. Try a wider date range or a different area."
        />
      );
    }

    return (
      <div className="flex flex-col gap-3 p-3">
        <div className="flex items-center justify-between gap-2">
          <p className="text-xs text-muted">
            <span className="font-medium text-fg">{result.resolved}</span> of {result.requested}{" "}
            {result.mode === "dates" ? "dates resolved" : "passes"} · {meta.label}
          </p>
          <button
            onClick={() => downloadCsv(passes, viewIndex, result.mode)}
            className="inline-flex items-center gap-1.5 rounded-md border border-border bg-panel px-2.5 py-1 text-xs text-muted transition-colors hover:bg-panel-2 hover:text-fg focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-accent"
          >
            <DownloadSimple size={13} /> CSV
          </button>
        </div>

        <MeanSparkline passes={passes} index={viewIndex} />

        <div className="overflow-x-auto">
          <table className="w-full border-collapse text-xs">
            <thead>
              <tr className="border-b border-border text-left text-muted">
                {hasRequested ? <th className="py-1.5 pr-3 font-medium">Requested</th> : null}
                <th className="py-1.5 pr-3 font-medium">Pass</th>
                <th className="py-1.5 pr-3 text-right font-medium">Mean</th>
                <th className="py-1.5 pr-3 text-right font-medium">Min</th>
                <th className="py-1.5 pr-3 text-right font-medium">Max</th>
                <th className="py-1.5 pr-3 text-right font-medium">P10</th>
                <th className="py-1.5 pr-3 text-right font-medium">P90</th>
                <th className="py-1.5 pr-3 text-right font-medium">Clear</th>
                <th className="py-1.5 font-medium">Conf.</th>
              </tr>
            </thead>
            <tbody>
              {passes.map((p, i) => (
                <Row
                  key={`${p.requested_date ?? p.pass_date ?? p.scene_id ?? i}`}
                  pass={p}
                  index={viewIndex}
                  showRequested={hasRequested}
                />
              ))}
            </tbody>
          </table>
        </div>
      </div>
    );
  }

  return (
    <div className="flex flex-col h-full">
      {selectedIndex === "all" ? (
        <div className="flex border-b border-border bg-panel-2 px-3 py-1.5 gap-1.5 overflow-x-auto shrink-0">
          {INDICES.map((idxMeta) => {
            const job = jobs[idxMeta.key];
            const active = idxMeta.key === viewIndex;
            let statusIcon = null;

            if (job?.state === "queued" || job?.state === "running") {
              statusIcon = <Spinner size={12} className="animate-spin text-accent" />;
            } else if (job?.state === "error") {
              statusIcon = <span className="size-2 rounded-full bg-critical" title="Failed" />;
            } else if (job?.state === "done") {
              statusIcon = <span className="size-2 rounded-full bg-positive" title="Done" />;
            }

            return (
              <button
                key={idxMeta.key}
                onClick={() => onViewIndexChange(idxMeta.key)}
                className={cn(
                  "flex items-center gap-1.5 rounded-md px-3 py-1 text-xs font-medium transition-colors border",
                  active
                    ? "border-accent bg-accent/15 text-accent"
                    : "border-border bg-panel text-muted hover:text-fg"
                )}
              >
                {idxMeta.label}
                {statusIcon}
              </button>
            );
          })}
        </div>
      ) : null}

      <div className="flex-1 min-h-0 overflow-y-auto">
        {renderJobContent()}
      </div>
    </div>
  );
}

function RunningState({ job }: { job: AOIJob | undefined }) {
  const done = job?.progress?.done ?? null;
  const total = job?.progress?.total ?? null;
  const pct = done !== null && total !== null && total > 0 ? Math.round((done / total) * 100) : null;

  return (
    <div className="flex h-full flex-col items-center justify-center gap-3 p-8 text-center">
      <Spinner size={22} className="animate-spin text-accent" />
      <p className="text-sm font-medium text-fg">
        {total !== null ? `Analysing pass ${done ?? 0} of ${total}` : "Starting analysis…"}
      </p>
      {pct !== null ? (
        <div className="h-1.5 w-48 overflow-hidden rounded-full bg-panel-2">
          <div
            className="h-full rounded-full bg-accent transition-[width] duration-300 ease-out"
            style={{ width: `${pct}%` }}
          />
        </div>
      ) : null}
      <p className="max-w-[44ch] text-xs leading-relaxed text-muted">
        Each pass is fetched and masked through the production engine. Nothing is saved.
      </p>
    </div>
  );
}

function Row({
  pass,
  index,
  showRequested,
}: {
  pass: AOISeriesPass;
  index: IndexKey;
  showRequested: boolean;
}) {
  const meta = indexMeta(index);
  const ok = pass.status === "ok";
  const interpolated = pass.status === "interpolated";

  if (!ok && !interpolated) {
    const hasNearest = pass.before || pass.after;
    return (
      <tr className="border-b border-border/60 text-muted">
        {showRequested ? (
          <td className="py-1.5 pr-3 tabular-nums text-fg">
            {pass.requested_date ? formatDate(pass.requested_date) : "·"}
          </td>
        ) : null}
        <td className="py-1.5 pr-3" colSpan={8}>
          <div className="flex flex-col gap-1 sm:flex-row sm:items-center sm:gap-2">
            <Badge tone="neutral" className="w-fit text-[10px] uppercase">
              no pass
            </Badge>
            {hasNearest && (
              <span className="text-[11px] text-muted">
                Nearest passes:{" "}
                {pass.before ? (
                  <>
                    <span className="font-semibold text-fg">{formatNumber(pass.before.mean)}</span> on{" "}
                    <span className="font-medium text-fg">{pass.before.pass_date ? formatDate(pass.before.pass_date) : "·"}</span>
                  </>
                ) : (
                  "none"
                )}
                {" and "}
                {pass.after ? (
                  <>
                    <span className="font-semibold text-fg">{formatNumber(pass.after.mean)}</span> on{" "}
                    <span className="font-medium text-fg">{pass.after.pass_date ? formatDate(pass.after.pass_date) : "·"}</span>
                  </>
                ) : (
                  "none"
                )}
              </span>
            )}
          </div>
        </td>
      </tr>
    );
  }

  const passCell = interpolated ? (
    <span
      title={
        pass.before_pass_date && pass.after_pass_date
          ? `Averaged from ${formatDate(pass.before_pass_date)} and ${formatDate(pass.after_pass_date)}`
          : "Averaged from two nearest passes"
      }
    >
      <Badge tone="caution" className="text-[10px] uppercase">
        avg
      </Badge>
    </span>
  ) : (
    <span className="tabular-nums">{pass.pass_date ? formatDate(pass.pass_date) : "·"}</span>
  );

  return (
    <tr className="border-b border-border/60">
      {showRequested ? (
        <td className="py-1.5 pr-3 tabular-nums text-muted">
          {pass.requested_date ? formatDate(pass.requested_date) : "·"}
        </td>
      ) : null}
      <td className="py-1.5 pr-3 text-fg">{passCell}</td>
      <td className="py-1.5 pr-3 text-right tabular-nums">
        <span className="inline-flex items-center justify-end gap-1.5">
          {pass.mean != null ? (
            <span
              aria-hidden
              className="size-2 shrink-0 rounded-full"
              style={{ background: colorForValue(meta, pass.mean) }}
            />
          ) : null}
          <span className="text-fg">{formatNumber(pass.mean)}</span>
        </span>
      </td>
      <td className="py-1.5 pr-3 text-right tabular-nums text-muted">{formatNumber(pass.min)}</td>
      <td className="py-1.5 pr-3 text-right tabular-nums text-muted">{formatNumber(pass.max)}</td>
      <td className="py-1.5 pr-3 text-right tabular-nums text-muted">{formatNumber(pass.p10)}</td>
      <td className="py-1.5 pr-3 text-right tabular-nums text-muted">{formatNumber(pass.p90)}</td>
      <td className="py-1.5 pr-3 text-right tabular-nums text-muted">
        {formatPercent(pass.clear_fraction)}
      </td>
      <td className="py-1.5">
        {pass.confidence ? (
          <Badge tone={confidenceTone(pass.confidence)} className="text-[10px] uppercase">
            {pass.confidence}
          </Badge>
        ) : (
          "·"
        )}
      </td>
    </tr>
  );
}

function confidenceTone(confidence: string): "positive" | "caution" | "critical" | "neutral" {
  if (confidence === "high") return "positive";
  if (confidence === "medium") return "caution";
  if (confidence === "low") return "critical";
  return "neutral";
}

const CHART_W = 560;
const CHART_H = 150;
const PAD = { l: 34, r: 12, t: 12, b: 22 };

/** A compact scatter+line of each usable pass's mean across time, on the index's display range, so
 *  the analyst can read the trend at a glance. Exact passes are filled circles; interpolated
 *  (averaged) passes are hollow circles so the analyst can distinguish synthesised data. */
function MeanSparkline({ passes, index }: { passes: AOISeriesPass[]; index: IndexKey }) {
  const meta = indexMeta(index);
  const points = useMemo(
    () =>
      passes
        .filter(
          (p) => (p.status === "ok" || p.status === "interpolated") && p.mean != null,
        )
        .map((p) => ({
          x: dateValue(p.requested_date ?? p.pass_date ?? ""),
          v: p.mean as number,
          date: p.requested_date ?? p.pass_date ?? "",
          interpolated: p.status === "interpolated",
        }))
        .filter((p) => p.date !== "")
        .sort((a, b) => a.x - b.x),
    [passes],
  );

  if (points.length === 0) return null;

  const xs = points.map((p) => p.x);
  const xMin = Math.min(...xs);
  const xMax = Math.max(...xs);
  const xSpan = xMax - xMin || 1;
  const sx = (x: number) =>
    points.length === 1
      ? (PAD.l + (CHART_W - PAD.r)) / 2
      : PAD.l + ((x - xMin) / xSpan) * (CHART_W - PAD.l - PAD.r);
  const ySpan = meta.max - meta.min || 1;
  const sy = (v: number) => {
    const t = Math.min(1, Math.max(0, (v - meta.min) / ySpan));
    return CHART_H - PAD.b - t * (CHART_H - PAD.t - PAD.b);
  };

  const path = points.map((p, i) => `${i === 0 ? "M" : "L"} ${sx(p.x)} ${sy(p.v)}`).join(" ");

  return (
    <svg
      viewBox={`0 0 ${CHART_W} ${CHART_H}`}
      className="h-auto w-full"
      role="img"
      aria-label={`${meta.label} mean over time, ${points.length} passes`}
    >
      {/* y bounds */}
      {[meta.min, meta.max].map((v) => (
        <g key={v}>
          <line
            x1={PAD.l}
            x2={CHART_W - PAD.r}
            y1={sy(v)}
            y2={sy(v)}
            stroke="var(--border)"
            strokeWidth={1}
          />
          <text x={4} y={sy(v) + 3} fill="var(--muted)" fontSize={9}>
            {v.toFixed(1)}
          </text>
        </g>
      ))}
      {/* trend line then points */}
      {points.length > 1 ? (
        <path d={path} fill="none" stroke="var(--muted)" strokeWidth={1.5} strokeOpacity={0.5} />
      ) : null}
      {points.map((p) =>
        p.interpolated ? (
          <circle
            key={p.date}
            cx={sx(p.x)}
            cy={sy(p.v)}
            r={3.5}
            fill="none"
            stroke={colorForValue(meta, p.v)}
            strokeWidth={1.5}
          >
            <title>{`${formatDate(p.date)} (averaged): ${formatNumber(p.v)}`}</title>
          </circle>
        ) : (
          <circle key={p.date} cx={sx(p.x)} cy={sy(p.v)} r={3.5} fill={colorForValue(meta, p.v)}>
            <title>{`${formatDate(p.date)}: ${formatNumber(p.v)}`}</title>
          </circle>
        ),
      )}
      {/* first/last date labels */}
      <text x={PAD.l} y={CHART_H - 6} fill="var(--muted)" fontSize={9}>
        {formatDate(points[0].date)}
      </text>
      {points.length > 1 ? (
        <text x={CHART_W - PAD.r} y={CHART_H - 6} fill="var(--muted)" fontSize={9} textAnchor="end">
          {formatDate(points[points.length - 1].date)}
        </text>
      ) : null}
    </svg>
  );
}

function downloadCsv(passes: AOISeriesPass[], index: IndexKey, mode: string): void {
  const header = [
    "requested_date",
    "pass_date",
    "before_pass_date",
    "after_pass_date",
    "status",
    "mean",
    "min",
    "max",
    "p10",
    "p90",
    "clear_fraction",
    "confidence",
    "resolution_m",
    "pixels",
    "scene_id",
  ];
  const cell = (v: unknown) => (v === null || v === undefined ? "" : String(v));
  const rows = passes.map((p) =>
    [
      p.requested_date,
      p.pass_date,
      p.before_pass_date,
      p.after_pass_date,
      p.status,
      p.mean,
      p.min,
      p.max,
      p.p10,
      p.p90,
      p.clear_fraction,
      p.confidence,
      p.resolution_m,
      p.pixels,
      p.scene_id,
    ]
      .map(cell)
      .join(","),
  );
  const csv = [header.join(","), ...rows].join("\n");
  const blob = new Blob([csv], { type: "text/csv;charset=utf-8" });
  const url = URL.createObjectURL(blob);
  const a = document.createElement("a");
  a.href = url;
  a.download = `aoi-${index}-${mode}.csv`;
  a.click();
  URL.revokeObjectURL(url);
}
