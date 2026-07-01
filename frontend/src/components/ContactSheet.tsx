import { CaretLeft, CaretRight } from "@phosphor-icons/react";
import { useEffect, useMemo, useRef, useState } from "react";

import type { Field, Scene } from "@/lib/api";
import { config } from "@/lib/config";
import { cn, formatDate } from "@/lib/format";
import { activeRasterKey } from "@/lib/indices";
import { useLazyImage } from "@/lib/useLazyImage";
import { useWorkspace } from "@/state/workspace";

import { EmptyState, ErrorState } from "./states";
import { Skeleton } from "./ui";

const PAGE_SIZE = 24;
// Fewer columns on a phone-width stacked layout (backlog 0044's mobile-reflow requirement),
// scaling up as the panel gets the full workspace width back - same sm/md/lg idiom used by the
// other dense grids in this codebase (e.g. FieldOverview's stat grid).
const GRID_COLS = "grid-cols-2 sm:grid-cols-3 md:grid-cols-4 lg:grid-cols-5 xl:grid-cols-6";

interface ContactSheetProps {
  field: Field;
  scenes: Scene[];
  isLoading: boolean;
  isError: boolean;
  error: unknown;
  onRetry: () => void;
  onSelectPass: (passDate: string) => void;
}

/** Small-multiples grid of every pass's static thumbnail at the workspace's active composite, so
 *  an analyst can scan a whole season at a glance instead of scrubbing one pass at a time
 *  (backlog 0044). MapPanel owns the exclusivity with compare mode; this component only renders
 *  the grid for whichever field/scene list it is given. */
export function ContactSheet({
  field,
  scenes,
  isLoading,
  isError,
  error,
  onRetry,
  onSelectPass,
}: ContactSheetProps) {
  const { passDate } = useWorkspace();
  const [page, setPage] = useState(0);
  const scrollRef = useRef<HTMLDivElement | null>(null);

  // Newest first, matching SceneList's display order underneath the same unified timeline.
  const ordered = useMemo(() => [...scenes].reverse(), [scenes]);

  // A stale page number must never strand the analyst past the end of a shorter history, so land
  // back on the most recent page whenever the field (and therefore the whole list) changes.
  useEffect(() => setPage(0), [field.field_id]);

  const pageCount = Math.max(1, Math.ceil(ordered.length / PAGE_SIZE));
  const clampedPage = Math.min(page, pageCount - 1);
  const pageItems = ordered.slice(clampedPage * PAGE_SIZE, clampedPage * PAGE_SIZE + PAGE_SIZE);

  // Paging in swaps the whole cell set but the scroll container itself persists, so without this
  // a "next page" click while scrolled down would drop the analyst mid-way through unfamiliar
  // cells instead of starting them at the top of the new page.
  useEffect(() => {
    scrollRef.current?.scrollTo({ top: 0 });
  }, [clampedPage]);

  return (
    <div className="absolute inset-0 flex flex-col bg-bg">
      {/* pt-[72px] clears the AOI toolbar, pl-16 clears the left-side icon cluster - both float
           over this panel at fixed positions (see MapPanel), and both are opaque, so grid cells
           must start clear of them rather than render (and sit unclickable) underneath. */}
      <div ref={scrollRef} className="min-h-0 flex-1 overflow-y-auto pb-3 pl-16 pr-3 pt-[72px]">
        {isLoading ? (
          <div className={cn("grid content-start gap-2.5", GRID_COLS)}>
            {Array.from({ length: 12 }).map((_, i) => (
              <Skeleton key={i} className="aspect-square w-full" />
            ))}
          </div>
        ) : isError ? (
          <ErrorState error={error} onRetry={onRetry} />
        ) : !ordered.length ? (
          <EmptyState title="No passes" hint="No collected passes for this field yet." />
        ) : (
          <div className={cn("grid content-start gap-2.5", GRID_COLS)}>
            {pageItems.map((scene) => (
              <ContactSheetCell
                key={scene.scene_id}
                field={field}
                scene={scene}
                active={scene.pass_date === passDate}
                onSelect={() => onSelectPass(scene.pass_date)}
              />
            ))}
          </div>
        )}
      </div>

      {!isLoading && !isError && pageCount > 1 ? (
        <div className="flex shrink-0 items-center justify-center gap-3 border-t border-border bg-panel/60 py-2 text-xs text-muted backdrop-blur-sm">
          <button
            onClick={() => setPage((p) => Math.max(0, p - 1))}
            disabled={clampedPage === 0}
            aria-label="Newer passes"
            className="inline-flex size-7 items-center justify-center rounded-md border border-border bg-panel text-muted transition-colors hover:bg-panel-2 hover:text-fg disabled:opacity-40 focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-accent"
          >
            <CaretLeft size={13} />
          </button>
          <span className="tnum">
            Page {clampedPage + 1} of {pageCount} · {ordered.length} passes
          </span>
          <button
            onClick={() => setPage((p) => Math.min(pageCount - 1, p + 1))}
            disabled={clampedPage === pageCount - 1}
            aria-label="Older passes"
            className="inline-flex size-7 items-center justify-center rounded-md border border-border bg-panel text-muted transition-colors hover:bg-panel-2 hover:text-fg disabled:opacity-40 focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-accent"
          >
            <CaretRight size={13} />
          </button>
        </div>
      ) : null}
    </div>
  );
}

function ContactSheetCell({
  field,
  scene,
  active,
  onSelect,
}: {
  field: Field;
  scene: Scene;
  active: boolean;
  onSelect: () => void;
}) {
  const { index, showRgb, showFcc } = useWorkspace();
  const [imgLoaded, setImgLoaded] = useState(false);
  const [imgError, setImgError] = useState(false);

  // Same precedence and static-thumbnail endpoint as SceneList's SceneRow, so a cell always shows
  // whatever composite the analyst currently has picked.
  const activeRaster = activeRasterKey(index, showRgb, showFcc);
  const tilerUrl = `${config.tilerBaseUrl}/static/${activeRaster}/${field.geometry_version}/${encodeURIComponent(field.field_id)}/${encodeURIComponent(scene.scene_id)}.jpg`;
  const { ref, src } = useLazyImage(tilerUrl);

  useEffect(() => {
    setImgLoaded(false);
    setImgError(false);
  }, [src]);

  return (
    <button
      onClick={onSelect}
      title={`${formatDate(scene.pass_date)} · ${scene.scene_id} · ${Math.round(scene.clear_fraction * 100)}% clear`}
      className={cn(
        "flex flex-col overflow-hidden rounded-md border text-left transition-colors duration-150 focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-accent",
        active ? "border-accent bg-accent/10" : "border-border bg-panel hover:border-accent/50",
      )}
    >
      {/* Thumbnail (lazy-loaded from the tiler, same three-state pattern as SceneList's SceneRow:
           pulsing placeholder while loading, the image once it resolves, or - on error / no COG
           at this index - a bare placeholder swatch, never a broken-image icon). */}
      <div
        ref={ref as (el: HTMLDivElement | null) => void}
        className="relative aspect-square w-full overflow-hidden bg-panel-2"
        aria-hidden
      >
        {src && !imgError ? (
          <img
            src={src}
            alt=""
            className={cn(
              "size-full object-cover transition-opacity duration-200",
              imgLoaded ? "opacity-100" : "opacity-0",
            )}
            onLoad={() => setImgLoaded(true)}
            onError={() => setImgError(true)}
          />
        ) : null}
        {(!src || (!imgLoaded && !imgError)) && !imgError ? (
          <div className="absolute inset-0 animate-pulse bg-border/20" />
        ) : null}
      </div>

      <div className="flex items-center justify-between gap-1 px-1.5 py-1">
        <span className="truncate text-[11px] text-fg">{formatDate(scene.pass_date)}</span>
        <span className="tnum shrink-0 text-[10px] text-muted">
          {Math.round(scene.clear_fraction * 100)}%
        </span>
      </div>
    </button>
  );
}
