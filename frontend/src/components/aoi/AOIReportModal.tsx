import { CloudArrowUp, FileText, X } from "@phosphor-icons/react";
import { useEffect } from "react";

import type { AOIJob, AOISeriesMode } from "@/lib/api";
import { formatDate, formatNumber, formatPercent } from "@/lib/format";
import { colorForValue, INDICES, type IndexKey } from "@/lib/indices";

import { confidenceTone, MeanSparkline } from "./AOIResultsTable";
import { EmptyState } from "../states";
import { Badge, Button } from "../ui";

interface AOIReportModalProps {
  /** Human label for what was analysed: a farm name in whole-farm mode, else "Custom area". */
  targetLabel: string;
  mode: AOISeriesMode;
  dates: string[];
  months: number;
  jobs: Record<IndexKey, AOIJob | undefined>;
  onClose: () => void;
  /** Called when the analyst clicks "Send to gateway". Closes this modal and opens the send
   *  surface. Only rendered when true. */
  canSend?: boolean;
  onSend?: () => void;
}

/** A read-only, on-screen summary of a finished custom-date / backfill run, spanning every index
 *  that produced usable passes. The analyst can trigger gateway send from the footer when results
 *  are ready; passing `onSend` opens the shared GatewaySendModal flow without navigating away.
 *  The numbers are the same resolved passes shown in the results table, grouped per index with the
 *  trend sparkline above each. */
export function AOIReportModal({
  targetLabel,
  mode,
  dates,
  months,
  jobs,
  onClose,
  canSend,
  onSend,
}: AOIReportModalProps) {
  useEffect(() => {
    const onKey = (e: KeyboardEvent) => {
      if (e.key === "Escape") onClose();
    };
    window.addEventListener("keydown", onKey);
    return () => window.removeEventListener("keydown", onKey);
  }, [onClose]);

  const sections = INDICES.map((meta) => {
    const job = jobs[meta.key];
    const result = job?.state === "done" ? (job.result ?? null) : null;
    const passes = (result?.passes ?? []).filter(
      (p) => p.status === "ok" || p.status === "interpolated",
    );
    return result && passes.length > 0 ? { meta, result, passes } : null;
  }).filter((x): x is NonNullable<typeof x> => x !== null);

  const allDates = sections
    .flatMap((s) => s.passes.map((p) => p.requested_date ?? p.pass_date ?? ""))
    .filter(Boolean)
    .sort();
  const span =
    allDates.length > 0
      ? `${formatDate(allDates[0])} to ${formatDate(allDates[allDates.length - 1])}`
      : null;
  const generatedAt = new Intl.DateTimeFormat("en-GB", {
    dateStyle: "medium",
    timeStyle: "short",
    timeZone: "Africa/Harare",
  }).format(new Date());

  return (
    <div className="fixed inset-0 z-50 flex items-start justify-center bg-bg/60 p-4 backdrop-blur-sm sm:p-8">
      <div
        role="dialog"
        aria-modal="true"
        aria-label="Analysis report"
        className="flex max-h-full w-full max-w-3xl flex-col overflow-hidden rounded-xl border border-border bg-panel shadow-xl"
      >
        <div className="flex items-center justify-between gap-3 border-b border-border px-4 py-3">
          <div className="flex items-center gap-2 text-sm font-semibold text-fg">
            <FileText size={18} className="text-accent" />
            <span>Analysis report</span>
          </div>
          <button
            onClick={onClose}
            aria-label="Close report"
            className="rounded-md p-1 text-muted transition-colors hover:bg-panel-2 hover:text-fg"
          >
            <X size={16} />
          </button>
        </div>

        <div className="min-h-0 flex-1 overflow-y-auto">
          <div className="border-b border-border bg-panel-2 px-4 py-3">
            <p className="text-sm font-semibold text-fg">{targetLabel}</p>
            <div className="mt-1 flex flex-wrap gap-x-4 gap-y-1 text-xs text-muted">
              <span>
                {mode === "dates"
                  ? `${dates.length} requested ${dates.length === 1 ? "date" : "dates"}`
                  : `Backfill · ${months} ${months === 1 ? "month" : "months"}`}
              </span>
              {span ? <span>{span}</span> : null}
              <span>Generated {generatedAt} CAT</span>
            </div>
          </div>

          {sections.length === 0 ? (
            <EmptyState
              title="No results to report"
              hint="Run an analysis first, then open the report."
            />
          ) : (
            <div className="flex flex-col">
              {sections.map(({ meta, result, passes }) => {
                const hasRequested = passes.some((p) => p.requested_date);
                return (
                  <section key={meta.key} className="border-b border-border px-4 py-4 last:border-b-0">
                    <div className="flex items-baseline justify-between gap-2">
                      <h3 className="text-sm font-semibold text-fg">
                        {meta.label}
                        <span className="ml-2 text-xs font-normal text-muted">{meta.long}</span>
                      </h3>
                      <span className="text-xs text-muted">
                        <span className="font-medium text-fg">{result.resolved}</span> of{" "}
                        {result.requested} {result.mode === "dates" ? "dates" : "passes"}
                      </span>
                    </div>

                    <div className="mt-2">
                      <MeanSparkline passes={passes} index={meta.key} />
                    </div>

                    <div className="mt-2 overflow-x-auto">
                      <table className="w-full border-collapse text-xs">
                        <thead>
                          <tr className="border-b border-border text-left text-muted">
                            {hasRequested ? (
                              <th className="py-1.5 pr-3 font-medium">Requested</th>
                            ) : null}
                            <th className="py-1.5 pr-3 font-medium">Pass</th>
                            <th className="py-1.5 pr-3 text-right font-medium">Mean</th>
                            <th className="py-1.5 pr-3 text-right font-medium">Min</th>
                            <th className="py-1.5 pr-3 text-right font-medium">Max</th>
                            <th className="py-1.5 pr-3 text-right font-medium">Clear</th>
                            <th className="py-1.5 font-medium">Conf.</th>
                          </tr>
                        </thead>
                        <tbody>
                          {passes.map((p, i) => {
                            const interpolated = p.status === "interpolated";
                            return (
                              <tr
                                key={`${p.requested_date ?? p.pass_date ?? p.scene_id ?? i}`}
                                className="border-b border-border/60"
                              >
                                {hasRequested ? (
                                  <td className="py-1.5 pr-3 tabular-nums text-muted">
                                    {p.requested_date ? formatDate(p.requested_date) : "·"}
                                  </td>
                                ) : null}
                                <td className="py-1.5 pr-3 text-fg">
                                  {interpolated ? (
                                    <Badge tone="caution" className="text-[10px] uppercase">
                                      avg
                                    </Badge>
                                  ) : (
                                    <span className="tabular-nums">
                                      {p.pass_date ? formatDate(p.pass_date) : "·"}
                                    </span>
                                  )}
                                </td>
                                <td className="py-1.5 pr-3 text-right tabular-nums">
                                  <span className="inline-flex items-center justify-end gap-1.5">
                                    {p.mean != null ? (
                                      <span
                                        aria-hidden
                                        className="size-2 shrink-0 rounded-full"
                                        style={{ background: colorForValue(meta, p.mean) }}
                                      />
                                    ) : null}
                                    <span className="text-fg">{formatNumber(p.mean)}</span>
                                  </span>
                                </td>
                                <td className="py-1.5 pr-3 text-right tabular-nums text-muted">
                                  {formatNumber(p.min)}
                                </td>
                                <td className="py-1.5 pr-3 text-right tabular-nums text-muted">
                                  {formatNumber(p.max)}
                                </td>
                                <td className="py-1.5 pr-3 text-right tabular-nums text-muted">
                                  {formatPercent(p.clear_fraction)}
                                </td>
                                <td className="py-1.5">
                                  {p.confidence ? (
                                    <Badge
                                      tone={confidenceTone(p.confidence)}
                                      className="text-[10px] uppercase"
                                    >
                                      {p.confidence}
                                    </Badge>
                                  ) : (
                                    "·"
                                  )}
                                </td>
                              </tr>
                            );
                          })}
                        </tbody>
                      </table>
                    </div>
                  </section>
                );
              })}
            </div>
          )}
        </div>

        <div className="flex items-center justify-between gap-3 border-t border-border px-4 py-3">
          <p className="text-[11px] text-muted">Preview only. Results are not persisted locally.</p>
          <div className="flex items-center gap-2">
            {onSend ? (
              <Button
                variant="outline"
                onClick={onSend}
                disabled={!canSend}
                title={canSend ? undefined : "No exact passes ready to send yet"}
                className="gap-1.5"
              >
                <CloudArrowUp size={14} />
                Send to gateway
              </Button>
            ) : null}
            <Button variant="primary" onClick={onClose} className="gap-1.5">
              Done
            </Button>
          </div>
        </div>
      </div>
    </div>
  );
}
