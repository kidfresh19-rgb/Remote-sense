import { DownloadSimple } from "@phosphor-icons/react";
import { useEffect, useState } from "react";

import { useToken } from "@/auth/TokenProvider";
import type { ResolvedPass } from "@/lib/api";
import { config } from "@/lib/config";
import { cn, formatDate } from "@/lib/format";
import { activeRasterKey } from "@/lib/indices";
import { useAsOf, useScenes } from "@/lib/queries";
import { useLazyImage } from "@/lib/useLazyImage";
import { useWorkspace } from "@/state/workspace";

import { EmptyState, ErrorState, LoadingRows } from "./states";
import { TimelineScrubber } from "./TimelineScrubber";

export function SceneList({
  fieldId,
  geometryVersion,
  collecting = false,
}: {
  fieldId: string;
  geometryVersion?: number;
  collecting?: boolean;
}) {
  const { token } = useToken();
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
        {[...scenes].reverse().map((scene) => (
          <li key={scene.scene_id}>
            <SceneRow
              fieldId={fieldId}
              sceneId={scene.scene_id}
              passDate={scene.pass_date}
              clearFraction={scene.clear_fraction}
              geometryVersion={geometryVersion}
              active={scene.pass_date === passDate}
              token={token}
              onSelect={() => setPassDate(scene.pass_date)}
            />
          </li>
        ))}
      </ul>
    </div>
  );
}

function SceneRow({
  fieldId,
  sceneId,
  passDate,
  clearFraction,
  geometryVersion,
  active,
  token,
  onSelect,
}: {
  fieldId: string;
  sceneId: string;
  passDate: string;
  clearFraction: number;
  geometryVersion?: number;
  active: boolean;
  token: string | null;
  onSelect: () => void;
}) {
  const { index, showRgb, showFcc } = useWorkspace();
  const [imgLoaded, setImgLoaded] = useState(false);
  const [imgError, setImgError] = useState(false);
  const [downloading, setDownloading] = useState(false);

  // activeRasterKey mirrors the main map's composite precedence (FCC > RGB > index) so the
  // thumbnail matches whatever composite the analyst has picked. Unlike the main map, a
  // thumbnail must never render blank, so it always resolves to something - never the map's
  // "none" state.
  const activeRaster = activeRasterKey(index, showRgb, showFcc);

  const tilerUrl =
    geometryVersion != null
      ? `${config.tilerBaseUrl}/static/${activeRaster}/${geometryVersion}/${encodeURIComponent(fieldId)}/${encodeURIComponent(sceneId)}.jpg`
      : null;
  const { ref, src } = useLazyImage(tilerUrl);

  useEffect(() => {
    setImgLoaded(false);
    setImgError(false);
  }, [src]);

  const downloadCog = async (e: React.MouseEvent) => {
    e.stopPropagation();
    if (!token || geometryVersion == null) return;
    setDownloading(true);
    try {
      const url =
        `${config.apiBaseUrl}/fields/${encodeURIComponent(fieldId)}/scenes/${encodeURIComponent(sceneId)}/download` +
        `?index=${encodeURIComponent(index)}&geometry_version=${geometryVersion}`;
      const resp = await fetch(url, { headers: { Authorization: `Bearer ${token}` } });
      if (!resp.ok) return; // 404 = no COG yet; silently skip
      const blob = await resp.blob();
      const blobUrl = URL.createObjectURL(blob);
      const a = document.createElement("a");
      a.href = blobUrl;
      a.download = `${index}_${sceneId}_gv${geometryVersion}.tif`;
      a.click();
      URL.revokeObjectURL(blobUrl);
    } finally {
      setDownloading(false);
    }
  };

  return (
    <div
      className={cn(
        "flex w-full items-center gap-2.5 px-2.5 py-2 transition-colors duration-150",
        active ? "bg-accent/10" : "hover:bg-panel-2",
      )}
    >
      {/* Natural colour thumbnail (lazy-loaded from tiler) */}
      <div
        ref={ref as (el: HTMLDivElement | null) => void}
        className="relative size-12 shrink-0 overflow-hidden rounded-md border border-border bg-panel-2"
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

      {/* Row text — full click area selects this pass */}
      <button
        onClick={onSelect}
        className="min-w-0 flex-1 text-left focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-inset focus-visible:ring-accent"
      >
        <p className="text-sm text-fg">{formatDate(passDate)}</p>
        <p className="truncate font-mono text-[11px] text-muted">{sceneId}</p>
        <p className="text-[11px] text-muted">{Math.round(clearFraction * 100)}% clear</p>
      </button>

      {/* Actions */}
      <div className="flex shrink-0 items-center gap-1.5">
        {geometryVersion != null && token ? (
          <button
            onClick={downloadCog}
            disabled={downloading}
            className="inline-flex size-7 items-center justify-center rounded-md border border-border bg-panel text-muted transition-colors hover:bg-panel-2 hover:text-fg focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-accent disabled:opacity-40 active:scale-95"
            title={`Download ${index.toUpperCase()} GeoTIFF`}
          >
            <DownloadSimple size={13} />
          </button>
        ) : null}
        {active ? <span className="size-2 rounded-full bg-accent" /> : null}
      </div>
    </div>
  );
}

function gapLabel(dayGap: number): string {
  if (dayGap === 0) return "on the requested date";
  const days = Math.abs(dayGap);
  return `${days} d ${dayGap < 0 ? "before" : "after"}`;
}

function AsOfPicker({ fieldId }: { fieldId: string }) {
  const { index, passDate, requestedDate, setRequestedDate, applyResolvedPass } = useWorkspace();
  const asOf = useAsOf(fieldId, requestedDate, index);
  const resolution = asOf.data ?? null;
  const resolvedDate = resolution?.resolved?.pass_date ?? null;

  useEffect(() => {
    if (resolvedDate) applyResolvedPass(resolvedDate);
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [resolvedDate]);

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
        <p className="mt-1.5 text-[11px] text-muted">Resolving...</p>
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
