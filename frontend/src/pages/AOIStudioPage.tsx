import {
  ArrowSquareOut,
  Crosshair,
  FloppyDisk,
  House,
  Lightning,
  MapTrifold,
  Moon,
  Plant,
  Stack,
  Sun,
  SignOut,
  X,
} from "@phosphor-icons/react";
import { Link } from "@tanstack/react-router";
import type { Geometry, Polygon } from "geojson";
import { useRef, useState } from "react";

import { TokenGate } from "@/auth/TokenGate";
import { useToken } from "@/auth/TokenProvider";
import { AOIBar } from "@/components/AOIBar";
import { AOIResultsTable } from "@/components/aoi/AOIResultsTable";
import { CoordinateEntryModal } from "@/components/CoordinateEntryModal";
import { DateBatchInput } from "@/components/DateBatchInput";
import { FarmFieldPickerModal } from "@/components/FarmFieldPickerModal";
import { FarmPushButton } from "@/components/FarmPushButton";
import { FileUploadPanel } from "@/components/FileUploadPanel";
import { bboxOf, useFieldMap } from "@/components/useFieldMap";
import { Badge, Button, IconButton, SegmentedControl } from "@/components/ui";
import { useFarmPush } from "@/lib/useFarmPush";
import { api, type AOIJob, type AOISeriesMode, type Farm } from "@/lib/api";
import { deleteCustomAOI, saveCustomAOI, useCustomAOIs } from "@/lib/customAOIs";
import { cn } from "@/lib/format";
import { DEFAULT_INDEX, INDICES, type IndexKey } from "@/lib/indices";
import { useAOIJob, useFarms, usePushAOIResults } from "@/lib/queries";
import { useTheme } from "@/lib/theme";

// Mirrors MAX_BATCH_DATES in services/api/workspace/analyse.py — keep them in step.
const MAX_BATCH_DATES = 24;
const MAX_BACKFILL_MONTHS = 18;

export function AOIStudioPage() {
  const { token } = useToken();

  return (
    <div className="grid h-[100dvh] max-h-[100dvh] grid-rows-[auto_minmax(0,1fr)] overflow-hidden bg-bg text-fg">
      <StudioHeader />
      {token ? <Studio /> : <TokenGate />}
    </div>
  );
}

function StudioHeader() {
  const { token, clear } = useToken();
  const { theme, toggle } = useTheme();

  return (
    <header className="flex h-14 items-center justify-between gap-4 border-b border-border bg-panel px-4">
      <div className="flex min-w-0 items-center gap-2">
        <MapTrifold size={20} weight="duotone" className="shrink-0 text-accent" />
        <span className="text-sm font-semibold tracking-tight">remote-sense</span>
        <span className="hidden text-xs text-muted sm:inline">AOI Studio</span>
      </div>
      <div className="flex items-center gap-2">
        <Link to="/">
          <IconButton label="Dashboard overview">
            <House size={18} />
          </IconButton>
        </Link>
        <Link to="/workspace">
          <IconButton label="Analyst workspace">
            <Stack size={18} />
          </IconButton>
        </Link>
        <IconButton
          label={theme === "dark" ? "Switch to light theme" : "Switch to dark theme"}
          onClick={toggle}
        >
          {theme === "dark" ? <Sun size={18} /> : <Moon size={18} />}
        </IconButton>
        {token ? (
          <IconButton label="Sign out" onClick={clear}>
            <SignOut size={18} />
          </IconButton>
        ) : null}
      </div>
    </header>
  );
}

/**
 * When the user picks a whole farm via the picker, we store a `FarmTarget` instead of a raw
 * Geometry so the Studio can call the farm-series endpoint (server-side union) rather than
 * constructing a client-side AOI.  Either `aoi` (custom geometry) or `farmTarget` (whole farm)
 * is non-null at any one time, never both.
 */
interface FarmTarget {
  canonicalFarmId: string;
  label: string;
}

type SelectedIndex = IndexKey | "all";

function Studio() {
  const { token } = useToken();
  const [aoi, setAoi] = useState<Geometry | null>(null);
  const [farmTarget, setFarmTarget] = useState<FarmTarget | null>(null);
  const [index, setIndex] = useState<SelectedIndex>(DEFAULT_INDEX);
  const [mode, setMode] = useState<AOISeriesMode>("dates");
  const [dates, setDates] = useState<string[]>([]);
  const [months, setMonths] = useState(6);

  // Track job ID per index
  const [jobIds, setJobIds] = useState<Record<IndexKey, string | null>>({
    ndvi: null,
    evi2: null,
    savi: null,
    ndre: null,
    ndmi: null,
  });

  // Local state to track which index we are currently viewing in the results tab.
  const [viewIndex, setViewIndex] = useState<IndexKey>("ndvi");

  // Farm chosen in the custom-AOI "push to gateway" panel; the push targets the viewed index's job.
  const [selectedFarmId, setSelectedFarmId] = useState<string>("");

  const [drawMode, setDrawMode] = useState(false);
  const [showCoords, setShowCoords] = useState(false);
  const [showUpload, setShowUpload] = useState(false);
  const [showFarms, setShowFarms] = useState(false);
  const [marker, setMarker] = useState<[number, number] | null>(null);

  const flyToRef = useRef<((center: [number, number], zoom?: number) => void) | null>(null);
  const fitBoundsRef = useRef<((sw: [number, number], ne: [number, number]) => void) | null>(null);

  const [running, setRunning] = useState(false);
  const [localError, setLocalError] = useState<Error | null>(null);

  // Call useAOIJob unconditionally for all 5 indices to satisfy the Rules of Hooks
  const ndviJob = useAOIJob(jobIds.ndvi);
  const evi2Job = useAOIJob(jobIds.evi2);
  const saviJob = useAOIJob(jobIds.savi);
  const ndreJob = useAOIJob(jobIds.ndre);
  const ndmiJob = useAOIJob(jobIds.ndmi);

  const jobs: Record<IndexKey, ReturnType<typeof useAOIJob>> = {
    ndvi: ndviJob,
    evi2: evi2Job,
    savi: saviJob,
    ndre: ndreJob,
    ndmi: ndmiJob,
  };

  const busy =
    running ||
    Object.values(jobs).some((j) => j.data?.state === "queued" || j.data?.state === "running");

  const canRun =
    !busy &&
    (aoi !== null || farmTarget !== null) &&
    (mode === "backfill" || dates.length > 0);

  // Whole-farm push (FarmPushButton) reuses the standard /farms/{id}/publish state machine.
  const pushState = useFarmPush(farmTarget?.canonicalFarmId ?? "");

  const hasJobs = Object.values(jobIds).some((id) => id !== null);
  const allJobsDone = hasJobs && Object.entries(jobIds)
    .filter(([_, id]) => id !== null)
    .every(([key, _]) => jobs[key as IndexKey].data?.state === "done");

  // Custom-AOI preview push (POST /analyse/aoi/jobs/{id}/push): push the viewed index's resolved
  // 'ok' passes to the gateway under a chosen farm. Only surfaced when a custom AOI is analysed;
  // whole-farm targets use the FarmPushButton publish above instead.
  const farms = useFarms();
  const push = usePushAOIResults();
  const viewedJob = jobs[viewIndex];
  const result = viewedJob.data?.state === "done" ? (viewedJob.data.result ?? null) : null;
  const okPassCount = result?.passes.filter((p) => p.status === "ok").length ?? 0;
  const canPush =
    !!jobIds[viewIndex] &&
    result !== null &&
    okPassCount > 0 &&
    !!selectedFarmId &&
    !push.isPending;

  /** Set a custom drawn / uploaded / geocoded AOI, clearing any farm target. */
  const setAoiAndClearPin = (geometry: Geometry) => {
    setMarker(null);
    setFarmTarget(null);
    setAoi(geometry);
    const bbox = bboxOf(geometry);
    if (bbox) fitBoundsRef.current?.([bbox[0], bbox[1]], [bbox[2], bbox[3]]);
  };

  /** Set the whole-farm target, clearing any custom AOI. */
  const setFarmTargetAndClear = (farm: Farm) => {
    setAoi(null);
    setMarker(null);
    setFarmTarget({ canonicalFarmId: farm.canonical_farm_id, label: farm.name ?? farm.canonical_farm_id });
    setJobIds({ ndvi: null, evi2: null, savi: null, ndre: null, ndmi: null });
    setLocalError(null);
    push.reset();
  };

  const clearTarget = () => {
    setAoi(null);
    setFarmTarget(null);
    setJobIds({ ndvi: null, evi2: null, savi: null, ndre: null, ndmi: null });
    setLocalError(null);
    push.reset();
  };

  const handleRun = async () => {
    const indicesToRun: IndexKey[] = index === "all"
      ? ["ndvi", "evi2", "savi", "ndre", "ndmi"]
      : [index];

    setLocalError(null);
    push.reset();
    setRunning(true);

    // Clear previous job ids for the indices we are running
    setJobIds((prev) => {
      const next = { ...prev };
      for (const idx of indicesToRun) {
        next[idx] = null;
      }
      return next;
    });

    if (index !== "all") {
      setViewIndex(index);
    } else {
      setViewIndex("ndvi");
    }

    try {
      await Promise.all(
        indicesToRun.map(async (idx) => {
          if (farmTarget) {
            const data = await api.analyseFarmSeries(
              farmTarget.canonicalFarmId,
              mode === "dates"
                ? { index: idx, mode, dates }
                : { index: idx, mode, months },
              token!
            );
            setJobIds((prev) => ({ ...prev, [idx]: data.job_id }));
          } else if (aoi) {
            const data = await api.analyseAOISeries(
              mode === "dates"
                ? { geometry: aoi, index: idx, mode, dates }
                : { geometry: aoi, index: idx, mode, months },
              token!
            );
            setJobIds((prev) => ({ ...prev, [idx]: data.job_id }));
          }
        })
      );
    } catch (err) {
      setLocalError(err instanceof Error ? err : new Error("Could not start the analysis."));
    } finally {
      setRunning(false);
    }
  };

  /** Push the resolved 'ok' passes of the currently-viewed index's job to the gateway. */
  const handlePush = () => {
    const jobId = jobIds[viewIndex];
    if (!jobId || !selectedFarmId) return;
    push.mutate({ jobId, req: { canonical_farm_id: selectedFarmId } });
  };

  const runError = localError;

  return (
    <main className="flex min-h-0 flex-col">
      <div className="flex min-h-0 flex-1 flex-col lg:flex-row">
        {/* Map */}
        <section className="relative min-h-[300px] flex-1 lg:min-h-0">
          <StudioMap
            aoi={aoi}
            index={index === "all" ? viewIndex : index}
            marker={marker}
            drawMode={drawMode}
            onDrawComplete={(polygon) => {
              setAoi(polygon);
              setFarmTarget(null);
              setDrawMode(false);
            }}
            onDrawCancel={() => setDrawMode(false)}
            onMapReady={(flyTo, fitBounds) => {
              flyToRef.current = flyTo;
              fitBoundsRef.current = fitBounds;
            }}
          />

          <div className="absolute inset-x-0 top-0 z-30 p-2">
            <AOIBar
              drawActive={drawMode}
              onDrawToggle={() => setDrawMode(true)}
              onCancelDraw={() => setDrawMode(false)}
              onOpenCoords={() => setShowCoords(true)}
              onOpenUpload={() => setShowUpload(true)}
              onOpenFarms={() => setShowFarms(true)}
              onAOISet={(geometry) => setAoiAndClearPin(geometry)}
              onFlyTo={(center, zoom) => {
                flyToRef.current?.(center, zoom);
                setMarker(center);
              }}
            />
          </div>

          {(aoi || farmTarget) && !drawMode ? (
            <div className="pointer-events-none absolute inset-x-0 top-[72px] z-20 flex justify-center">
              <button
                onClick={clearTarget}
                className="pointer-events-auto flex items-center gap-1.5 rounded-full border border-border bg-panel/90 px-3 py-1 text-xs text-muted backdrop-blur-sm transition-colors hover:text-fg"
              >
                <X size={12} /> Clear area
              </button>
            </div>
          ) : null}

          {showCoords ? (
            <CoordinateEntryModal
              onClose={() => setShowCoords(false)}
              onApply={(geometry) => setAoiAndClearPin(geometry)}
              onFitBounds={(sw, ne) => {
                fitBoundsRef.current?.(sw, ne);
                setMarker(null);
              }}
            />
          ) : null}
          {showUpload ? (
            <FileUploadPanel
              onClose={() => setShowUpload(false)}
              onApply={(geometry, label) => {
                setAoiAndClearPin(geometry);
                saveCustomAOI({ label, geometry });
              }}
            />
          ) : null}
          {showFarms ? (
            <FarmFieldPickerModal
              onClose={() => setShowFarms(false)}
              onPickField={(field) => setAoiAndClearPin(field.geometry)}
              onPickFarm={(farm) => setFarmTargetAndClear(farm)}
            />
          ) : null}
        </section>

        {/* Controls */}
        <aside className="flex min-h-0 shrink-0 flex-col gap-4 overflow-y-auto border-t border-border bg-panel p-4 lg:w-[360px] lg:border-l lg:border-t-0">
          <AreaSection
            aoi={aoi}
            farmTarget={farmTarget}
            onClear={clearTarget}
            onUse={setAoiAndClearPin}
          />

          <div className="flex flex-col gap-2">
            <Label>Index</Label>
            <SegmentedControl<SelectedIndex>
              ariaLabel="Index"
              value={index}
              onChange={setIndex}
              options={[
                ...INDICES.map((m) => ({ value: m.key, label: m.label, title: m.long })),
                { value: "all", label: "All Indices", title: "Run NDVI, EVI2, SAVI, NDRE, and NDMI at once" }
              ]}
            />
          </div>

          <div className="flex flex-col gap-2">
            <Label>Mode</Label>
            <SegmentedControl<AOISeriesMode>
              ariaLabel="Analysis mode"
              value={mode}
              onChange={setMode}
              options={[
                { value: "dates", label: "Specific dates" },
                { value: "backfill", label: "Backfill" },
              ]}
            />
          </div>

          {mode === "dates" ? (
            <div className="flex flex-col gap-2">
              <Label>Dates ({dates.length})</Label>
              <DateBatchInput dates={dates} onChange={setDates} max={MAX_BATCH_DATES} />
              <p className="text-[11px] text-muted">
                Each date resolves to its same-day pass; if none exists, the two nearest passes are
                averaged.
              </p>
            </div>
          ) : (
            <BackfillControl months={months} onChange={setMonths} />
          )}

          {!busy && !aoi && !farmTarget ? (
            <div className="rounded-md border border-caution/20 bg-caution/10 p-2.5 text-xs text-caution leading-normal">
              <strong>Area required:</strong> Draw an area on the map, upload a boundary, or pick a farm/field using the leaf button to start.
            </div>
          ) : !busy && mode === "dates" && dates.length === 0 ? (
            <div className="rounded-md border border-caution/20 bg-caution/10 p-2.5 text-xs text-caution leading-normal">
              <strong>Dates required:</strong> Please select at least one target date above to run analysis.
            </div>
          ) : null}

          <div
            className="w-full mt-1"
            title={
              !aoi && !farmTarget
                ? "Select an area first"
                : mode === "dates" && dates.length === 0
                  ? "Select at least one date"
                  : undefined
            }
          >
            <Button
              variant="primary"
              onClick={handleRun}
              disabled={!canRun}
              className="w-full gap-1.5"
            >
              <Lightning size={14} weight="fill" />
              {busy
                ? "Analysing…"
                : mode === "dates"
                  ? `Run ${dates.length || ""} ${dates.length === 1 ? "date" : "dates"}`.trim()
                  : "Start backfill"}
            </Button>
          </div>
          {runError ? (
            <p className="text-xs text-critical">
              {runError instanceof Error ? runError.message : "Could not start the analysis."}
            </p>
          ) : null}

          {farmTarget && allJobsDone && (
            <div className="mt-3 border-t border-border pt-3 flex flex-col gap-2 animate-in fade-in slide-in-from-bottom-2 duration-200">
              <Label>Gateway Sync</Label>
              <FarmPushButton push={pushState} className="w-full gap-1.5" />
            </div>
          )}

          {!farmTarget && result !== null && okPassCount > 0 ? (
            <div className="flex flex-col gap-2 border-t border-border pt-4">
              <Label>Push to gateway</Label>
              <p className="text-[11px] text-muted">
                Push the {INDICES.find((m) => m.key === viewIndex)?.label ?? viewIndex} preview passes
                to the gateway under a farm.
              </p>
              <select
                value={selectedFarmId}
                onChange={(e) => {
                  setSelectedFarmId(e.target.value);
                  push.reset();
                }}
                className="w-full rounded-md border border-border bg-bg px-2.5 py-1.5 text-xs text-fg focus:outline-none focus:ring-2 focus:ring-accent"
                aria-label="Farm to push results under"
              >
                <option value="">Select a farm…</option>
                {(farms.data ?? []).map((f) => (
                  <option key={f.canonical_farm_id} value={f.canonical_farm_id}>
                    {f.name ?? f.canonical_farm_id}
                  </option>
                ))}
              </select>
              <Button
                variant="outline"
                onClick={handlePush}
                disabled={!canPush}
                className="w-full gap-1.5"
              >
                <ArrowSquareOut size={14} />
                {push.isPending
                  ? "Pushing…"
                  : `Push ${okPassCount} ${okPassCount === 1 ? "pass" : "passes"} to gateway`}
              </Button>
              {push.isSuccess ? (
                <p className="text-xs text-positive">
                  {push.data.dry_run
                    ? `Recorded ${push.data.pushed_passes} passes (dry-run)`
                    : `Sent ${push.data.pushed_passes} passes`}
                </p>
              ) : null}
              {push.isError ? (
                <p className="text-xs text-critical">
                  {push.error instanceof Error ? push.error.message : "Push failed"}
                </p>
              ) : null}
            </div>
          ) : null}
        </aside>
      </div>

      {/* Results */}
      <section className="max-h-[44vh] shrink-0 overflow-y-auto border-t border-border bg-panel">
        <AOIResultsTable
          jobs={Object.fromEntries(
            Object.entries(jobs).map(([k, v]) => [k, v.data])
          ) as Record<IndexKey, AOIJob | undefined>}
          pending={running}
          selectedIndex={index}
          viewIndex={viewIndex}
          onViewIndexChange={setViewIndex}
        />
      </section>
    </main>
  );
}

function Label({ children }: { children: React.ReactNode }) {
  return (
    <span className="text-xs font-semibold uppercase tracking-wide text-muted">{children}</span>
  );
}

function AreaSection({
  aoi,
  farmTarget,
  onClear,
  onUse,
}: {
  aoi: Geometry | null;
  farmTarget: FarmTarget | null;
  onClear: () => void;
  onUse: (geometry: Geometry) => void;
}) {
  const saved = useCustomAOIs();

  return (
    <div className="flex flex-col gap-2">
      <Label>Area of interest</Label>
      {farmTarget ? (
        /* Whole-farm mode */
        <div className="flex items-center justify-between gap-2 rounded-md border border-border bg-bg px-2.5 py-1.5">
          <span className="flex items-center gap-1.5 text-sm text-fg">
            <Badge tone="accent" className="text-[10px] uppercase">
              Farm
            </Badge>
            <Plant size={13} weight="duotone" className="text-accent" />
            <span className="min-w-0 truncate">{farmTarget.label}</span>
          </span>
          <IconButton label="Clear farm target" onClick={onClear} className="size-7 shrink-0">
            <X size={14} />
          </IconButton>
        </div>
      ) : aoi ? (
        /* Custom geometry mode */
        <div className="flex items-center justify-between gap-2 rounded-md border border-border bg-bg px-2.5 py-1.5">
          <span className="flex items-center gap-1.5 text-sm text-fg">
            <Badge tone="accent" className="text-[10px] uppercase">
              {aoi.type}
            </Badge>
            area selected
          </span>
          <div className="flex items-center gap-1">
            <IconButton
              label="Save this area to reuse later"
              onClick={() =>
                saveCustomAOI({ label: `AOI ${new Date().toLocaleDateString()}`, geometry: aoi })
              }
              className="size-7"
            >
              <FloppyDisk size={14} />
            </IconButton>
            <IconButton label="Clear area" onClick={onClear} className="size-7">
              <X size={14} />
            </IconButton>
          </div>
        </div>
      ) : (
        <p className="text-xs leading-relaxed text-muted">
          Draw, search, enter coordinates, or upload a boundary on the map; pick a saved area
          below; or use the <Plant size={11} className="inline" /> button to select a farm or
          field.
        </p>
      )}

      {saved.length > 0 ? (
        <ul className="max-h-32 overflow-y-auto rounded-md border border-border">
          {saved.map((a) => (
            <li
              key={a.id}
              className="flex items-center gap-1 border-b border-border/60 px-2 py-1 last:border-b-0"
            >
              <span className="min-w-0 flex-1 truncate text-xs text-fg">{a.label}</span>
              <IconButton
                label={`Use area ${a.label}`}
                onClick={() => onUse(a.geometry)}
                className="size-6"
              >
                <Crosshair size={12} />
              </IconButton>
              <IconButton
                label={`Delete area ${a.label}`}
                onClick={() => deleteCustomAOI(a.id)}
                className="size-6 text-muted hover:text-critical"
              >
                <X size={12} />
              </IconButton>
            </li>
          ))}
        </ul>
      ) : null}
    </div>
  );
}

function BackfillControl({
  months,
  onChange,
}: {
  months: number;
  onChange: (next: number) => void;
}) {
  return (
    <div className="flex flex-col gap-2">
      <Label>Backfill window</Label>
      <div className="flex items-center gap-3">
        <input
          type="range"
          min={1}
          max={MAX_BACKFILL_MONTHS}
          step={1}
          value={months}
          onChange={(e) => onChange(Number(e.target.value))}
          aria-label="Months of history"
          className="min-w-0 flex-1 accent-[var(--accent)]"
        />
        <span className="w-20 shrink-0 text-right text-sm tabular-nums text-fg">
          {months} {months === 1 ? "month" : "months"}
        </span>
      </div>
      <div className="flex gap-1.5">
        {[3, 6, 12, 18].map((m) => (
          <button
            key={m}
            onClick={() => onChange(m)}
            className={cn(
              "rounded-md border px-2 py-0.5 text-xs transition-colors",
              months === m
                ? "border-accent bg-accent/15 text-accent"
                : "border-border text-muted hover:text-fg",
            )}
          >
            {m}m
          </button>
        ))}
      </div>
      <p className="text-[11px] text-muted">
        Sweeps every usable pass in the window (most recent first, up to 60).
      </p>
    </div>
  );
}

function StudioMap({
  aoi,
  index,
  marker,
  drawMode,
  onDrawComplete,
  onDrawCancel,
  onMapReady,
}: {
  aoi: Geometry | null;
  index: IndexKey;
  marker: [number, number] | null;
  drawMode: boolean;
  onDrawComplete: (polygon: Polygon) => void;
  onDrawCancel: () => void;
  onMapReady: (
    flyTo: (center: [number, number], zoom?: number) => void,
    fitBounds: (sw: [number, number], ne: [number, number]) => void,
  ) => void;
}) {
  const ref = useRef<HTMLDivElement | null>(null);
  useFieldMap(ref, {
    field: null,
    index,
    sceneId: null,
    showRaster: false,
    showRgb: false,
    showFcc: false,
    customAOI: aoi,
    marker,
    drawMode,
    onDrawComplete,
    onDrawCancel,
    onMapReady,
  });
  // size-full (not absolute inset-0): MapLibre's unlayered `position:relative` beats Tailwind's
  // layered `absolute`, which would collapse the container to 0 height (see useFieldMap notes).
  return <div ref={ref} className="size-full" />;
}
