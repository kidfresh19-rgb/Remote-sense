import {
  CloudArrowUp,
  Crosshair,
  FileText,
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
import { getRouteApi, Link } from "@tanstack/react-router";
import type { Geometry, Polygon } from "geojson";
import { useEffect, useMemo, useRef, useState } from "react";

import { TokenGate } from "@/auth/TokenGate";
import { useToken } from "@/auth/TokenProvider";
import { AOIBar } from "@/components/AOIBar";
import { AOIReportModal } from "@/components/aoi/AOIReportModal";
import { GatewaySendModal } from "@/components/aoi/GatewaySendModal";
import { AOIResultsTable } from "@/components/aoi/AOIResultsTable";
import { CoordinateEntryModal } from "@/components/CoordinateEntryModal";
import { DateBatchInput } from "@/components/DateBatchInput";
import { FarmFieldPickerModal } from "@/components/FarmFieldPickerModal";
import { FileUploadPanel } from "@/components/FileUploadPanel";
import { bboxOf, useFieldMap } from "@/components/useFieldMap";
import { Badge, Button, IconButton, SegmentedControl } from "@/components/ui";
import { useFarmPush } from "@/lib/useFarmPush";
import {
  api,
  type AOIJob,
  type AOIJobEnqueued,
  type AOISeriesMode,
  type AOISeriesResult,
  type Farm,
  type MultiIndexResult,
} from "@/lib/api";
import { deleteCustomAOI, saveCustomAOI, useCustomAOIs } from "@/lib/customAOIs";
import { cn } from "@/lib/format";
import { DEFAULT_INDEX, INDICES, type IndexKey } from "@/lib/indices";
import { useAOIJob, useFarms, usePushAllAOIResults, usePushAOIResults } from "@/lib/queries";
import { useTheme } from "@/lib/theme";

const routeApi = getRouteApi("/aoi-studio");

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
  const { farm: farmParam } = routeApi.useSearch();
  const [aoi, setAoi] = useState<Geometry | null>(null);
  const [farmTarget, setFarmTarget] = useState<FarmTarget | null>(null);
  const [index, setIndex] = useState<SelectedIndex>(DEFAULT_INDEX);
  const [mode, setMode] = useState<AOISeriesMode>("dates");
  const [dates, setDates] = useState<string[]>([]);
  const [months, setMonths] = useState(6);

  // One job covers the whole run: a single index, or all indices in one task (ADR 0011 Phase 2).
  // `ranIndices` records which indices that job carries so the single result can be unpacked back
  // into the per-index shape the table, report, and send surfaces consume.
  const [jobId, setJobId] = useState<string | null>(null);
  const [ranIndices, setRanIndices] = useState<IndexKey[]>([]);

  // Local state to track which index we are currently viewing in the results tab.
  const [viewIndex, setViewIndex] = useState<IndexKey>("ndvi");

  // Farm chosen in the custom-AOI "push to gateway" panel; the push targets the viewed index's job.
  const [selectedFarmId, setSelectedFarmId] = useState<string>("");

  const [drawMode, setDrawMode] = useState(false);
  const [showCoords, setShowCoords] = useState(false);
  const [showUpload, setShowUpload] = useState(false);
  const [showFarms, setShowFarms] = useState(false);
  const [showReport, setShowReport] = useState(false);
  const [showSend, setShowSend] = useState(false);
  const [marker, setMarker] = useState<[number, number] | null>(null);

  // Once-only guard so a `?farm=` deep-link pre-selects that farm without re-applying after the
  // analyst later clears or changes the target.
  const appliedFarmParam = useRef<string | null>(null);

  const flyToRef = useRef<((center: [number, number], zoom?: number) => void) | null>(null);
  const fitBoundsRef = useRef<((sw: [number, number], ne: [number, number]) => void) | null>(null);

  const [running, setRunning] = useState(false);
  const [localError, setLocalError] = useState<Error | null>(null);

  const job = useAOIJob(jobId);

  // Unpack the one polled job into the per-index map the results UI consumes. The poll returns a
  // flat AOISeriesResult for a single-index job, or {indices:{...}} for an all-indices job; AOIJob
  // is typed for the common single case, so we widen once here to discriminate (ADR 0011 Phase 2).
  const jobData = useMemo<Record<IndexKey, AOIJob | undefined>>(() => {
    const out: Partial<Record<IndexKey, AOIJob>> = {};
    const data = job.data;
    if (data) {
      const raw = data.result as AOISeriesResult | MultiIndexResult | null | undefined;
      for (const key of ranIndices) {
        if (data.state === "done" && raw) {
          const perIndex = "indices" in raw ? raw.indices[key] : raw;
          out[key] = { ...data, result: perIndex ?? null };
        } else {
          // queued / running / error are shared across every index the job covers.
          out[key] = data;
        }
      }
    }
    return out as Record<IndexKey, AOIJob | undefined>;
  }, [job.data, ranIndices]);

  const busy =
    running || job.data?.state === "queued" || job.data?.state === "running";

  const canRun =
    !busy &&
    (aoi !== null || farmTarget !== null) &&
    (mode === "backfill" || dates.length > 0);

  // Whole-farm push (FarmPushButton) reuses the standard /farms/{id}/publish state machine.
  const pushState = useFarmPush(farmTarget?.canonicalFarmId ?? "");


  // Custom-AOI preview push (POST /analyse/aoi/jobs/{id}/push): push the viewed index's resolved
  // 'ok' passes to the gateway under a chosen farm. Only surfaced when a custom AOI is analysed;
  // whole-farm targets use FarmPushButton (via GatewaySendModal) instead.
  const farms = useFarms();
  const push = usePushAOIResults();
  const pushAll = usePushAllAOIResults();
  const viewedJob = jobData[viewIndex];
  const result = viewedJob?.state === "done" ? (viewedJob.result ?? null) : null;
  const okPassCount =
    result?.passes.filter((p) => p.status === "ok" || p.status === "interpolated").length ?? 0;

  // Every index that finished with at least one exact/averaged pass: the set "Push all" sends. They
  // all share the one job id (its result already carries every index), so the send dedupes to a
  // single gateway push.
  const pushableIndexJobs = ranIndices
    .map((key) => {
      const data = jobData[key];
      const okCount =
        data?.state === "done"
          ? (data.result?.passes.filter(
              (p) => p.status === "ok" || p.status === "interpolated",
            ).length ?? 0)
          : 0;
      return jobId && okCount > 0 ? { key, jobId, okCount } : null;
    })
    .filter((x): x is { key: IndexKey; jobId: string; okCount: number } => x !== null);
  const totalOkPasses = pushableIndexJobs.reduce((sum, j) => sum + j.okCount, 0);

  const anyResults = Object.values(jobData).some(
    (j) => j?.state === "done" && (j.result?.passes.length ?? 0) > 0,
  );

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
    setJobId(null);
    setRanIndices([]);
    setLocalError(null);
    push.reset();
    pushAll.reset();
  };

  const clearTarget = () => {
    setAoi(null);
    setFarmTarget(null);
    setJobId(null);
    setRanIndices([]);
    setLocalError(null);
    push.reset();
    pushAll.reset();
  };

  // Apply a `?farm=` deep-link once the farm list has loaded: pre-select it as the analysis target.
  // The ref guard keeps this a one-time effect so the analyst can later clear the target freely.
  useEffect(() => {
    if (!farmParam || appliedFarmParam.current === farmParam) return;
    const match = (farms.data ?? []).find((f) => f.canonical_farm_id === farmParam);
    if (!match) return;
    appliedFarmParam.current = farmParam;
    setFarmTargetAndClear(match);
  }, [farmParam, farms.data]);

  const handleRun = async () => {
    const indicesToRun: IndexKey[] = index === "all"
      ? ["ndvi", "evi2", "savi", "ndre", "ndmi"]
      : [index];

    setLocalError(null);
    push.reset();
    pushAll.reset();
    setRunning(true);
    setJobId(null);
    setRanIndices(indicesToRun);
    setViewIndex(index === "all" ? "ndvi" : index);

    // One request, one job: a single `index`, or the all-indices `indices` list whose one task
    // shares a band read across every index (ADR 0011 Phase 2). The window is dates or months.
    const indexField = index === "all" ? { indices: indicesToRun } : { index };
    const seriesWindow = mode === "dates" ? { mode, dates } : { mode, months };

    try {
      let enqueued: AOIJobEnqueued;
      if (farmTarget) {
        enqueued = await api.analyseFarmSeries(
          farmTarget.canonicalFarmId,
          { ...indexField, ...seriesWindow },
          token!
        );
      } else if (aoi) {
        enqueued = await api.analyseAOISeries(
          { geometry: aoi, ...indexField, ...seriesWindow },
          token!
        );
      } else {
        return;
      }
      setJobId(enqueued.job_id);
    } catch (err) {
      setLocalError(err instanceof Error ? err : new Error("Could not start the analysis."));
    } finally {
      setRunning(false);
    }
  };

  /** Open the shared send surface. If the report is currently open, close it first so the send
   *  modal sits on top of a clean backdrop rather than stacking two dialogs. */
  const openSend = () => {
    setShowReport(false);
    setShowSend(true);
  };

  // Whether there is anything the send surface can act on right now.
  const canSendAnything =
    !!farmTarget || pushableIndexJobs.length > 0 || okPassCount > 0;

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

        </aside>
      </div>

      {/* Results */}
      <section className="flex max-h-[44vh] shrink-0 flex-col border-t border-border bg-panel">
        {anyResults ? (
          <div className="flex shrink-0 items-center justify-between gap-2 border-b border-border px-3 py-1.5">
            <span className="text-xs font-medium text-muted">Results</span>
            <div className="flex items-center gap-1.5">
              <Button
                variant="outline"
                onClick={() => setShowReport(true)}
                className="h-7 gap-1.5 px-2.5 text-xs"
              >
                <FileText size={13} /> Report
              </Button>
              <Button
                variant="outline"
                onClick={openSend}
                disabled={!canSendAnything}
                title={canSendAnything ? undefined : "No exact passes ready to send yet"}
                className="h-7 gap-1.5 px-2.5 text-xs"
              >
                <CloudArrowUp size={13} /> Send
              </Button>
            </div>
          </div>
        ) : null}
        <div className="min-h-0 flex-1 overflow-y-auto">
          <AOIResultsTable
            jobs={jobData}
            pending={running}
            selectedIndex={index}
            viewIndex={viewIndex}
            onViewIndexChange={setViewIndex}
            geometry={aoi}
            jobId={jobId}
          />
        </div>
      </section>

      {showReport ? (
        <AOIReportModal
          targetLabel={farmTarget ? farmTarget.label : "Custom area"}
          mode={mode}
          dates={dates}
          months={months}
          jobs={jobData}
          onClose={() => setShowReport(false)}
          canSend={canSendAnything}
          onSend={openSend}
        />
      ) : null}

      {showSend ? (
        <GatewaySendModal
          farmPush={farmTarget ? pushState : null}
          viewIndex={viewIndex}
          okPassCount={okPassCount}
          pushableIndexJobs={pushableIndexJobs}
          totalOkPasses={totalOkPasses}
          selectedFarmId={farmTarget ? farmTarget.canonicalFarmId : selectedFarmId}
          onSelectedFarmIdChange={(id) => {
            setSelectedFarmId(id);
            push.reset();
            pushAll.reset();
          }}
          farms={farms.data ?? []}
          push={push}
          pushAll={pushAll}
          viewedJobId={jobId}
          onClose={() => {
            setShowSend(false);
            push.reset();
            pushAll.reset();
          }}
        />
      ) : null}
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
