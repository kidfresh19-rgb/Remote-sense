import { Pulse, Warning } from "@phosphor-icons/react";

import { EmptyState, ErrorState, LoadingRows } from "@/components/states";
import { Badge } from "@/components/ui";
import { cn, formatNumber } from "@/lib/format";
import { useWardWatchTriage } from "@/lib/queries";
import { cohortLevelLabel, movementMeta } from "@/lib/wardwatch";

/** The ranked officer triage queue (PRD 0003 §7.1). Each row carries its movement label, robust
 *  deviation and the §4 honesty flags (small cohort / low pixel quality). When `onSelectHousehold`
 *  is given each row becomes a button that opens the household's visit cockpit. */
export function TriageQueue({
  ward,
  cap,
  onSelectHousehold,
}: {
  ward?: string;
  cap?: number;
  onSelectHousehold?: (householdId: string) => void;
}) {
  const queue = useWardWatchTriage({ ward, cap });
  const interactive = !!onSelectHousehold;

  return (
    <div className="flex h-full flex-col">
      <div className="flex items-center justify-between border-b border-border px-4 py-3">
        <div>
          <h2 className="text-sm font-semibold text-fg">Triage queue</h2>
          <p className="text-xs text-muted">Ranked by movement severity, worst first</p>
        </div>
        {queue.data ? (
          <Badge tone="neutral">
            {queue.data.length} household{queue.data.length === 1 ? "" : "s"}
          </Badge>
        ) : null}
      </div>
      <div className="flex-1 overflow-y-auto">
        {queue.isPending ? (
          <LoadingRows rows={8} />
        ) : queue.isError ? (
          <ErrorState error={queue.error} onRetry={() => queue.refetch()} />
        ) : queue.data.length === 0 ? (
          <EmptyState
            icon={<Pulse size={22} />}
            title="No households in the queue"
            hint="Either the cohort signal is steady, or no household has an assessable plot series yet. The queue is honest - it never fabricates a rank."
          />
        ) : (
          <ul className="divide-y divide-border">
            {queue.data.map((row) => {
              const meta = movementMeta(row.label);
              const flags = [
                !row.cohort_meets_quorum ? "small cohort (below quorum)" : null,
                row.low_pixel_quality ? "low pixel quality" : null,
              ].filter(Boolean) as string[];
              return (
                <li key={row.household_id}>
                  <button
                    type="button"
                    disabled={!interactive}
                    onClick={
                      onSelectHousehold ? () => onSelectHousehold(row.household_id) : undefined
                    }
                    className={cn(
                      "flex w-full items-center gap-3 px-4 py-3 text-left",
                      interactive ? "cursor-pointer hover:bg-panel-2" : "cursor-default",
                    )}
                  >
                    <span className="tnum w-6 shrink-0 text-sm font-semibold text-muted">
                      {row.rank}
                    </span>
                    <div className="min-w-0 flex-1">
                      <div className="flex items-center gap-2">
                        <p className="truncate text-sm font-medium text-fg">{row.household_id}</p>
                        <Badge tone={meta.tone}>{meta.label}</Badge>
                      </div>
                      <p className="mt-0.5 truncate text-xs text-muted">
                        {[
                          row.ward,
                          row.village,
                          row.dominant_crop,
                          cohortLevelLabel(row.cohort_level),
                        ]
                          .filter(Boolean)
                          .join(" · ")}
                      </p>
                    </div>
                    <div className="shrink-0 text-right">
                      <p className="tnum text-sm font-semibold text-fg">
                        {formatNumber(row.robust_deviation, 2)}
                      </p>
                      <p className="text-[10px] uppercase tracking-wide text-muted/70">deviation</p>
                    </div>
                    {flags.length > 0 ? (
                      <span title={flags.join("; ")} className="shrink-0">
                        <Warning
                          size={16}
                          weight="fill"
                          className="text-caution"
                          aria-label={flags.join("; ")}
                        />
                      </span>
                    ) : null}
                  </button>
                </li>
              );
            })}
          </ul>
        )}
      </div>
    </div>
  );
}
