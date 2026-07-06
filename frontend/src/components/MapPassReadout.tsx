import { CaretLeft, CaretRight } from "@phosphor-icons/react";
import type { ReactNode } from "react";

import type { TimeseriesPoint } from "@/lib/api";
import { cn, formatDate, formatNumber, formatPercent } from "@/lib/format";
import type { IndexMeta } from "@/lib/indices";

/** On-map pass control: names the pass currently drawn on the map, steps or jumps to any collected
 *  pass, and shows that pass's collected numbers (mean index, clear fraction, confidence) inline so
 *  the analyst never has to leave the map for the Series tab. Dates arrive oldest-first; the left
 *  caret steps back in time, the right caret forward. `points` carries the per-pass numbers keyed by
 *  pass date; a pass with imagery but no computed index for this layer shows a middle dot, not a
 *  fabricated value. */
export function MapPassReadout({
  meta,
  dates,
  value,
  onChange,
  points,
  clearByDate,
  side,
  className,
}: {
  meta: IndexMeta;
  /** Every collected pass date for the field, oldest first. */
  dates: string[];
  /** The selected pass date. Null falls back to the newest pass. */
  value: string | null;
  onChange: (date: string) => void;
  /** Per-pass index numbers keyed by pass date. */
  points: Map<string, TimeseriesPoint>;
  /** Scene clear fraction keyed by pass date, used when a pass has no index point yet. */
  clearByDate?: Map<string, number>;
  /** Optional A / B tag for the side-by-side comparison panes. */
  side?: "A" | "B";
  className?: string;
}) {
  if (!dates.length) return null;

  const idx = value ? dates.indexOf(value) : -1;
  const pos = idx >= 0 ? idx : dates.length - 1;
  const current = dates[pos];
  const canOlder = pos > 0;
  const canNewer = pos < dates.length - 1;

  const point = points.get(current) ?? null;
  const clear = point?.clear_fraction ?? clearByDate?.get(current) ?? null;
  const conf = point?.confidence ?? null;
  const confClass =
    conf === "high"
      ? "text-positive"
      : conf === "low"
        ? "text-critical"
        : conf
          ? "text-caution"
          : "text-muted";

  return (
    <div
      className={cn(
        "pointer-events-auto flex flex-col gap-1 rounded-lg border border-border bg-panel/90 px-2 py-1.5 shadow-sm backdrop-blur-sm",
        className,
      )}
    >
      {/* Date stepper — prev / jump / next across every collected pass */}
      <div className="flex items-center gap-1">
        {side ? <span className="px-0.5 text-xs font-semibold text-accent">{side}</span> : null}
        <StepButton
          label="Previous (older) pass"
          disabled={!canOlder}
          onClick={() => canOlder && onChange(dates[pos - 1])}
        >
          <CaretLeft size={13} weight="bold" />
        </StepButton>
        <div className="relative min-w-[96px] text-center">
          <span className="tnum pointer-events-none text-sm font-medium text-fg">
            {formatDate(current)}
          </span>
          <select
            aria-label={side ? `Pass ${side} date` : "Displayed pass date"}
            value={current}
            onChange={(e) => onChange(e.target.value)}
            className="absolute inset-0 cursor-pointer opacity-0"
            title="Jump to any collected pass"
          >
            {dates.map((d) => (
              <option key={d} value={d}>
                {formatDate(d)}
              </option>
            ))}
          </select>
        </div>
        <StepButton
          label="Next (newer) pass"
          disabled={!canNewer}
          onClick={() => canNewer && onChange(dates[pos + 1])}
        >
          <CaretRight size={13} weight="bold" />
        </StepButton>
        <span className="px-0.5 text-[10px] tabular-nums text-muted" aria-hidden>
          {pos + 1}/{dates.length}
        </span>
      </div>

      {/* Collected numbers for this pass — the same figures as the Series tab, on the map */}
      <div className="flex items-center justify-center gap-2.5 border-t border-border/50 pt-1 text-[11px] leading-none">
        <span className="tnum">
          <span className="text-muted">{meta.label} </span>
          <span className="font-semibold text-fg">{formatNumber(point?.mean ?? null)}</span>
        </span>
        <span className="tnum">
          <span className="text-muted">clear </span>
          <span className="text-fg">{formatPercent(clear)}</span>
        </span>
        {conf ? <span className={cn("font-medium capitalize", confClass)}>{conf}</span> : null}
      </div>
    </div>
  );
}

function StepButton({
  label,
  disabled,
  onClick,
  children,
}: {
  label: string;
  disabled: boolean;
  onClick: () => void;
  children: ReactNode;
}) {
  return (
    <button
      type="button"
      aria-label={label}
      title={label}
      disabled={disabled}
      onClick={onClick}
      className="inline-flex size-6 items-center justify-center rounded-md text-muted transition-colors hover:bg-panel-2 hover:text-fg focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-accent disabled:cursor-not-allowed disabled:opacity-30"
    >
      {children}
    </button>
  );
}
