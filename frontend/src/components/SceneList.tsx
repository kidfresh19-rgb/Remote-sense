import { useEffect } from "react";

import type { ResolvedPass } from "@/lib/api";
import { cn, formatDate } from "@/lib/format";
import { useAsOf, useScenes } from "@/lib/queries";
import { useWorkspace } from "@/state/workspace";

import { EmptyState, ErrorState, LoadingRows } from "./states";
import { TimelineScrubber } from "./TimelineScrubber";

export function SceneList({
  fieldId,
  collecting = false,
}: {
  fieldId: string;
  collecting?: boolean;
}) {
  const query = useScenes(fieldId, collecting);
  const { passDate, setPassDate } = useWorkspace();

  if (query.isLoading) return <LoadingRows />;
  if (query.isError) return <ErrorState error={query.error} onRetry={() => query.refetch()} />;
  const scenes = query.data ?? [];
  if (!scenes.length) {
    return (
      <EmptyState
        title="No passes"
        hint={
          collecting
            ? "Collecting passes. They appear here as each scene is processed."
            : "No collected passes for this field yet."
        }
      />
    );
  }

  return (
    <div className="flex flex-col">
      <div className="border-b border-border p-3">
        <TimelineScrubber
          dates={scenes.map((s) => s.pass_date)}
          value={passDate}
          onChange={setPassDate}
        />
      </div>
      <div className="border-b border-border p-3">
        <AsOfPicker fieldId={fieldId} />
      </div>
      <ul className="divide-y divide-border">
        {[...scenes].reverse().map((scene) => {
          const active = scene.pass_date === passDate;
          return (
            <li key={scene.scene_id}>
              <button
                onClick={() => setPassDate(scene.pass_date)}
                className={cn(
                  "flex w-full items-center justify-between gap-3 px-3 py-2 text-left ease-out transition-colors duration-150 focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-inset focus-visible:ring-accent",
                  active ? "bg-accent/10" : "hover:bg-panel-2",
                )}
              >
                <div className="min-w-0">
                  <p className="text-sm text-fg">{formatDate(scene.pass_date)}</p>
                  <p className="truncate font-mono text-[11px] text-muted">{scene.scene_id}</p>
                </div>
                <div className="flex shrink-0 items-center gap-2">
                  <span className="tnum text-[11px] text-muted">
                    {Math.round(scene.clear_fraction * 100)}% clear
                  </span>
                  {active ? <span className="size-2 rounded-full bg-accent" /> : null}
                </div>
              </button>
            </li>
          );
        })}
      </ul>
    </div>
  );
}

function gapLabel(dayGap: number): string {
  if (dayGap === 0) return "on the requested date";
  const days = Math.abs(dayGap);
  return `${days} d ${dayGap < 0 ? "before" : "after"}`;
}

/** Jump-to-date control (S3.1): resolve any calendar date to the field's nearest usable pass.
 *  The map always shows a real acquisition, labeled with the true date and the day gap; when a
 *  usable pass exists on the other side of the date too, one click flips to it. */
function AsOfPicker({ fieldId }: { fieldId: string }) {
  const { index, passDate, requestedDate, setRequestedDate, applyResolvedPass } = useWorkspace();
  const asOf = useAsOf(fieldId, requestedDate, index);
  const resolution = asOf.data ?? null;
  const resolvedDate = resolution?.resolved?.pass_date ?? null;

  // Apply the resolution when it lands or re-resolves (new date, or index change). The reducer
  // keeps requestedDate on applyResolvedPass, so the label below survives the application.
  useEffect(() => {
    if (resolvedDate) applyResolvedPass(resolvedDate);
    // applyResolvedPass is identity-unstable (context value rebuilds per state change); keying
    // on the resolved date alone is the intended "fire once per resolution" behavior.
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [resolvedDate]);

  // Label from what the map is actually showing, so the flip affordance stays truthful.
  const showing: ResolvedPass | null = resolution
    ? resolution.before?.pass_date === passDate
      ? resolution.before
      : resolution.after?.pass_date === passDate
        ? resolution.after
        : null
    : null;
  const other: ResolvedPass | null =
    resolution && showing
      ? showing === resolution.before
        ? resolution.after
        : resolution.before
      : null;

  return (
    <div>
      <div className="mb-1 flex items-center justify-between text-[10px] uppercase tracking-wide text-muted">
        <span>Jump to date</span>
        {requestedDate ? (
          <button
            onClick={() => setRequestedDate(null)}
            className="normal-case transition-colors hover:text-fg"
          >
            Clear
          </button>
        ) : null}
      </div>
      <input
        type="date"
        aria-label="Jump to date"
        value={requestedDate ?? ""}
        onChange={(e) => setRequestedDate(e.target.value || null)}
        className="tnum w-full rounded-md border border-border bg-panel-2 px-2 py-1.5 text-sm text-fg focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-accent"
      />
      {requestedDate && asOf.isLoading ? (
        <p className="mt-1.5 text-[11px] text-muted">Resolving…</p>
      ) : null}
      {requestedDate && showing ? (
        <p className="mt-1.5 text-[11px] text-muted">
          Showing <span className="text-fg">{formatDate(showing.pass_date)}</span>
          {" · "}
          {gapLabel(showing.day_gap)}
          {" · "}
          <span className="tnum">{Math.round(showing.clear_fraction * 100)}% clear</span>
          {other ? (
            <>
              {" · "}
              <button
                onClick={() => applyResolvedPass(other.pass_date)}
                className="text-accent transition-opacity hover:opacity-80"
              >
                use {formatDate(other.pass_date)}
              </button>
            </>
          ) : null}
        </p>
      ) : null}
      {requestedDate && resolution && !resolution.resolved ? (
        <p className="mt-1.5 text-[11px] text-muted">
          No usable pass near this date. Passes below 50% clear are not offered.
        </p>
      ) : null}
    </div>
  );
}
