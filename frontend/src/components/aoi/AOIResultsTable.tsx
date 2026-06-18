import { DownloadSimple, Spinner, ChartLine, Table as TableIcon, ArrowUp, ArrowDown, ArrowsOut, ArrowsIn, X } from "@phosphor-icons/react";
import { useMemo, useState, useCallback, useEffect } from "react";
import { createPortal } from "react-dom";
import { motion, AnimatePresence } from "motion/react";
import {
  AreaChart,
  Area,
  LineChart,
  Line,
  XAxis,
  YAxis,
  CartesianGrid,
  Tooltip,
  ResponsiveContainer,
  ReferenceLine,
} from "recharts";

import type { AOIJob, AOISeriesPass } from "@/lib/api";
import { cn, dateValue, formatDate, formatNumber, formatPercent } from "@/lib/format";
import { colorForValue, gradientCss, indexMeta, INDICES, type IndexKey } from "@/lib/indices";

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

type ViewMode = "chart" | "table";

export function AOIResultsTable({
  jobs,
  pending,
  selectedIndex,
  viewIndex,
  onViewIndexChange,
}: AOIResultsTableProps) {
  const meta = indexMeta(viewIndex);
  const hasAnyJob = Object.values(jobs).some((j) => !!j);
  const [viewMode, setViewMode] = useState<ViewMode>("chart");
  const [expanded, setExpanded] = useState(false);

  // Close on Escape
  useEffect(() => {
    if (!expanded) return;
    const onKey = (e: KeyboardEvent) => {
      if (e.key === "Escape") setExpanded(false);
    };
    document.addEventListener("keydown", onKey);
    // Prevent body scroll while expanded
    const prev = document.body.style.overflow;
    document.body.style.overflow = "hidden";
    return () => {
      document.removeEventListener("keydown", onKey);
      document.body.style.overflow = prev;
    };
  }, [expanded]);

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

    const content = (
      <div className={`flex flex-col gap-3 p-3 ${
        expanded ? "h-full overflow-y-auto" : ""
      }`}>
        {/* Header bar */}
        <div className="flex items-center justify-between gap-2">
          <p className="text-xs text-muted">
            <span className="font-medium text-fg">{result.resolved}</span> of {result.requested}{" "}
            {result.mode === "dates" ? "dates resolved" : "passes"} · {meta.label}
          </p>
          <div className="flex items-center gap-1.5">
            {/* View mode toggle */}
            <div className="inline-flex rounded-md border border-border bg-panel p-0.5">
              <button
                onClick={() => setViewMode("chart")}
                className={`rounded-[5px] px-2 py-1 text-xs font-medium transition-colors duration-150 ${
                  viewMode === "chart"
                    ? "bg-accent text-accent-fg"
                    : "text-muted hover:text-fg"
                }`}
                title="Chart view"
              >
                <ChartLine size={13} weight="bold" />
              </button>
              <button
                onClick={() => setViewMode("table")}
                className={`rounded-[5px] px-2 py-1 text-xs font-medium transition-colors duration-150 ${
                  viewMode === "table"
                    ? "bg-accent text-accent-fg"
                    : "text-muted hover:text-fg"
                }`}
                title="Table view"
              >
                <TableIcon size={13} weight="bold" />
              </button>
            </div>
            <button
              onClick={() => downloadCsv(passes, viewIndex, result.mode)}
              className="inline-flex items-center gap-1.5 rounded-md border border-border bg-panel px-2.5 py-1 text-xs text-muted transition-colors hover:bg-panel-2 hover:text-fg focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-accent"
            >
              <DownloadSimple size={13} /> CSV
            </button>
            {/* Expand / Collapse toggle */}
            <button
              onClick={() => setExpanded((v) => !v)}
              className="inline-flex items-center gap-1.5 rounded-md border border-border bg-panel px-2.5 py-1 text-xs text-muted transition-colors hover:bg-panel-2 hover:text-fg focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-accent"
              title={expanded ? "Collapse (Esc)" : "Expand to full screen"}
            >
              {expanded ? <ArrowsIn size={13} /> : <ArrowsOut size={13} />}
              {expanded ? "Collapse" : "Expand"}
            </button>
          </div>
        </div>

        {viewMode === "chart" ? (
          <ChartView passes={passes} index={viewIndex} />
        ) : (
          <TableView passes={passes} index={viewIndex} hasRequested={hasRequested} />
        )}

        {/* Agronomic insights — always visible below chart or table */}
        <IndexInsights passes={passes} index={viewIndex} />
      </div>
    );

    // Fullscreen portal overlay
    if (expanded) {
      return (
        <>
          {/* Placeholder keeps the original layout from collapsing */}
          <div className="flex items-center justify-center p-6 text-xs text-muted">
            <span className="inline-flex items-center gap-1.5">
              <ArrowsOut size={14} />
              Results expanded — press <kbd className="rounded border border-border bg-panel-2 px-1 py-0.5 font-mono text-[10px]">Esc</kbd> or click Collapse
            </span>
          </div>
          {createPortal(
            <AnimatePresence>
              <motion.div
                key="aoi-fullscreen"
                initial={{ opacity: 0, scale: 0.97 }}
                animate={{ opacity: 1, scale: 1 }}
                exit={{ opacity: 0, scale: 0.97 }}
                transition={{ duration: 0.2, ease: [0.22, 1, 0.36, 1] }}
                className="fixed inset-0 z-[9999] flex flex-col"
              >
                {/* Backdrop */}
                <motion.div
                  initial={{ opacity: 0 }}
                  animate={{ opacity: 1 }}
                  exit={{ opacity: 0 }}
                  transition={{ duration: 0.15 }}
                  className="absolute inset-0 modal-overlay"
                  onClick={() => setExpanded(false)}
                />
                {/* Panel */}
                <div className="relative z-10 m-3 flex flex-1 flex-col overflow-hidden rounded-2xl border border-border bg-panel shadow-2xl"
                  style={{ boxShadow: "0 8px 60px 0 rgb(0 0 0 / 0.25)" }}
                >
                  {/* Close corner button */}
                  <button
                    onClick={() => setExpanded(false)}
                    className="absolute right-3 top-3 z-20 inline-flex size-7 items-center justify-center rounded-md border border-border bg-panel text-muted transition-colors hover:bg-panel-2 hover:text-fg focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-accent"
                    title="Close (Esc)"
                  >
                    <X size={14} weight="bold" />
                  </button>
                  {content}
                </div>
              </motion.div>
            </AnimatePresence>,
            document.body,
          )}
        </>
      );
    }

    return content;
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

/* ─── Chart View ──────────────────────────────────────────────────────────── */

interface ChartPoint {
  date: string;
  dateLabel: string;
  dateMs: number;
  mean: number;
  min: number | null;
  max: number | null;
  p10: number | null;
  p90: number | null;
  clearFraction: number | null;
  confidence: string | null;
  interpolated: boolean;
  color: string;
}

function ChartView({ passes, index }: { passes: AOISeriesPass[]; index: IndexKey }) {
  const meta = indexMeta(index);

  const { points, highPoint, lowPoint, avgMean } = useMemo(() => {
    const pts: ChartPoint[] = passes
      .filter(
        (p) => (p.status === "ok" || p.status === "interpolated") && p.mean != null,
      )
      .map((p) => ({
        date: p.requested_date ?? p.pass_date ?? "",
        dateLabel: formatDate(p.requested_date ?? p.pass_date ?? ""),
        dateMs: dateValue(p.requested_date ?? p.pass_date ?? ""),
        mean: p.mean as number,
        min: p.min ?? null,
        max: p.max ?? null,
        p10: p.p10 ?? null,
        p90: p.p90 ?? null,
        clearFraction: p.clear_fraction ?? null,
        confidence: p.confidence ?? null,
        interpolated: p.status === "interpolated",
        color: colorForValue(meta, p.mean as number),
      }))
      .filter((p) => p.date !== "")
      .sort((a, b) => a.dateMs - b.dateMs);

    let high: ChartPoint | null = null;
    let low: ChartPoint | null = null;
    let sum = 0;
    for (const p of pts) {
      if (!high || p.mean > high.mean) high = p;
      if (!low || p.mean < low.mean) low = p;
      sum += p.mean;
    }
    return {
      points: pts,
      highPoint: high,
      lowPoint: low,
      avgMean: pts.length > 0 ? sum / pts.length : 0,
    };
  }, [passes, meta]);

  if (points.length === 0) return null;

  return (
    <div className="flex flex-col gap-3">
      {/* Stat summary cards */}
      <StatCards
        highPoint={highPoint}
        lowPoint={lowPoint}
        avgMean={avgMean}
        totalPasses={points.length}
        index={index}
      />

      {/* Main interactive chart */}
      <div className="rounded-lg border border-border bg-panel p-3">
        <p className="mb-2 text-[10px] font-medium uppercase tracking-wider text-muted">
          {meta.label} Mean over time
          <span className="ml-1.5 font-normal normal-case text-muted/70">
            — hover points for details
          </span>
        </p>
        <MainChart points={points} index={index} />
      </div>

      {/* Range band chart */}
      <div className="rounded-lg border border-border bg-panel p-3">
        <p className="mb-2 text-[10px] font-medium uppercase tracking-wider text-muted">
          Value Spread
          <span className="ml-1.5 font-normal normal-case text-muted/70">
            — min/max range with P10–P90 band
          </span>
        </p>
        <RangeChart points={points} index={index} />
      </div>

      {/* Heatmap strip */}
      <div className="rounded-lg border border-border bg-panel p-3">
        <p className="mb-2 text-[10px] font-medium uppercase tracking-wider text-muted">
          Temporal Heatmap
          <span className="ml-1.5 font-normal normal-case text-muted/70">
            — color intensity by value
          </span>
        </p>
        <HeatmapStrip points={points} index={index} />
      </div>
      </div>
    </div>
  );
}
/* ─── Index Insights ──────────────────────────────────────────────────────── */

/** Agronomic interpretation thresholds and context for each Sentinel-2 derived index.
 *  Values are calibrated against typical Sentinel-2 L2A reflectance for Southern African
 *  cropping systems; ranges overlap slightly because field conditions vary. */

interface ValueZone {
  label: string;
  min: number;
  max: number;
  tone: "critical" | "caution" | "neutral" | "positive";
}

interface IndexInsight {
  /** What this index actually measures (remote sensing context). */
  measures: string;
  /** What it tells a farmer or agronomist in plain terms. */
  farmContext: string;
  /** Sentinel-2 bands used in the formula. */
  bands: string;
  /** Value zones from poor to excellent. */
  zones: ValueZone[];
  /** Generate a readable assessment from the average mean. */
  assess: (avg: number) => string;
  /** Generate a trend message from first-half vs second-half means. */
  trend: (firstHalf: number, secondHalf: number) => string;
}

const INDEX_INSIGHTS: Record<string, IndexInsight> = {
  ndvi: {
    measures:
      "Chlorophyll absorption in red light vs. near-infrared reflectance. Healthy green vegetation absorbs red and strongly reflects NIR, producing high NDVI values.",
    farmContext:
      "The most widely used vegetation index for crop monitoring. It tracks canopy greenness and overall crop vigour across the growing season. Useful for spotting stressed patches, comparing fields, and timing harvest.",
    bands: "B4 (Red) & B8 (NIR) — 10 m resolution",
    zones: [
      { label: "Bare soil / water", min: -0.2, max: 0.1, tone: "critical" },
      { label: "Sparse / stressed vegetation", min: 0.1, max: 0.3, tone: "caution" },
      { label: "Moderate canopy", min: 0.3, max: 0.5, tone: "neutral" },
      { label: "Healthy crop", min: 0.5, max: 0.7, tone: "positive" },
      { label: "Dense, vigorous canopy", min: 0.7, max: 0.9, tone: "positive" },
    ],
    assess: (avg) => {
      if (avg < 0.1) return "Values indicate bare soil, fallow ground, or standing water. No meaningful vegetation is detected in this area.";
      if (avg < 0.25) return "Vegetation is very sparse or under significant stress. This could indicate early-stage planting, drought impact, or post-harvest residue.";
      if (avg < 0.4) return "Moderate greenness detected. Crops may be in early growth stages, or the canopy has not yet closed. Consider checking for nutrient deficiency or moisture stress.";
      if (avg < 0.6) return "Good canopy development with reasonable vigour. The crop is actively photosynthesising and appears to be developing normally for mid-season.";
      if (avg < 0.75) return "Healthy, well-established canopy. Strong chlorophyll activity indicates good growing conditions. The crop is performing well.";
      return "Very dense, highly vigorous vegetation. Peak growing conditions. Note: values above 0.8 may saturate in dense canopies — consider EVI2 for finer discrimination.";
    },
    trend: (first, second) => {
      const delta = second - first;
      if (delta > 0.08) return "Canopy greenness is increasing — consistent with active crop growth or recovery from stress.";
      if (delta < -0.08) return "Greenness is declining — this may indicate senescence, harvest, moisture stress, or disease pressure.";
      return "Greenness is relatively stable across the observation period.";
    },
  },
  evi2: {
    measures:
      "A two-band enhancement of NDVI that reduces atmospheric noise and soil background effects. It remains sensitive in dense canopies where NDVI saturates.",
    farmContext:
      "Better than NDVI for distinguishing vigour differences in high-biomass crops like maize at full canopy. When NDVI plateaus near 0.8+, EVI2 still differentiates between good and excellent growth.",
    bands: "B4 (Red) & B8 (NIR) — 10 m resolution",
    zones: [
      { label: "Bare / no vegetation", min: -0.1, max: 0.1, tone: "critical" },
      { label: "Low vegetation", min: 0.1, max: 0.25, tone: "caution" },
      { label: "Moderate canopy", min: 0.25, max: 0.4, tone: "neutral" },
      { label: "Healthy crop", min: 0.4, max: 0.55, tone: "positive" },
      { label: "Dense vigorous canopy", min: 0.55, max: 0.8, tone: "positive" },
    ],
    assess: (avg) => {
      if (avg < 0.1) return "No significant vegetation detected. The area is likely bare soil, recently harvested, or waterlogged.";
      if (avg < 0.25) return "Low vegetation cover. Crops may be in very early stages or stressed. EVI2 is more reliable than NDVI here due to soil background correction.";
      if (avg < 0.4) return "Moderate canopy development. The crop is growing but hasn't reached full vigour. Compare with NDVI — if NDVI is high but EVI2 is moderate, atmospheric conditions may be affecting readings.";
      if (avg < 0.55) return "Good crop vigour with healthy biomass accumulation. The enhanced sensitivity of EVI2 confirms genuine canopy health, not just surface reflectance.";
      return "Excellent canopy density and vigour. EVI2 is particularly valuable at this range because NDVI would likely be saturated, masking variability between good and excellent areas.";
    },
    trend: (first, second) => {
      const delta = second - first;
      if (delta > 0.06) return "Biomass is actively increasing — the crop is in a strong growth phase.";
      if (delta < -0.06) return "Biomass is declining — likely senescence, dry-down, or stress-related canopy loss.";
      return "Canopy density has been stable through the observed period.";
    },
  },
  savi: {
    measures:
      "Vegetation index with a soil brightness correction factor (L=0.5). Reduces the influence of exposed soil on the spectral signal, making it accurate in fields with incomplete canopy cover.",
    farmContext:
      "Essential for early-season monitoring when crops haven't closed the canopy and bare soil is visible between rows. Also valuable for orchards, vineyards, and any crop with permanent inter-row exposure.",
    bands: "B4 (Red) & B8 (NIR) — 10 m resolution, L=0.5 correction",
    zones: [
      { label: "Bare soil dominant", min: -0.1, max: 0.1, tone: "critical" },
      { label: "Sparse / early growth", min: 0.1, max: 0.2, tone: "caution" },
      { label: "Partial canopy cover", min: 0.2, max: 0.35, tone: "neutral" },
      { label: "Good cover", min: 0.35, max: 0.5, tone: "positive" },
      { label: "Full canopy", min: 0.5, max: 0.7, tone: "positive" },
    ],
    assess: (avg) => {
      if (avg < 0.1) return "Predominantly bare soil signal. No meaningful crop canopy is present. This is expected pre-planting or post-harvest.";
      if (avg < 0.2) return "Early-stage vegetation or very sparse cover. SAVI is the right index here — NDVI would overestimate due to soil brightness contamination.";
      if (avg < 0.35) return "Partial canopy cover developing. The soil correction is actively improving accuracy at this stage. Crop rows are likely still visible from above.";
      if (avg < 0.5) return "Good vegetation cover with the soil signal largely masked by the canopy. Growth is progressing well and the correction factor has less influence at this density.";
      return "Full canopy closure detected. At this density SAVI converges with NDVI — the soil correction is no longer needed as inter-row soil is fully shaded.";
    },
    trend: (first, second) => {
      const delta = second - first;
      if (delta > 0.05) return "Canopy is filling in — inter-row gaps are closing as the crop develops.";
      if (delta < -0.05) return "Cover is reducing — possible leaf drop, pest damage, or approaching harvest.";
      return "Canopy cover has remained consistent through the observation window.";
    },
  },
  ndre: {
    measures:
      "Ratio of red-edge (705 nm) to near-infrared reflectance. The red-edge band is sensitive to chlorophyll content and leaf structure at a cellular level, penetrating deeper into the canopy than the visible red band.",
    farmContext:
      "The best index for detecting nitrogen stress and chlorophyll content. A crop can appear green to the eye (decent NDVI) while NDRE reveals hidden nitrogen deficiency. Crucial for precision fertiliser management.",
    bands: "B5 (Red Edge 705 nm) & B8 (NIR) — 20 m resolution",
    zones: [
      { label: "Very low chlorophyll", min: -0.1, max: 0.05, tone: "critical" },
      { label: "Nitrogen stress likely", min: 0.05, max: 0.15, tone: "caution" },
      { label: "Moderate chlorophyll", min: 0.15, max: 0.3, tone: "neutral" },
      { label: "Healthy N status", min: 0.3, max: 0.45, tone: "positive" },
      { label: "High chlorophyll / N", min: 0.45, max: 0.6, tone: "positive" },
    ],
    assess: (avg) => {
      if (avg < 0.05) return "Extremely low chlorophyll signal. Vegetation is either absent or severely deficient. If crops are present, immediate investigation for nitrogen starvation is warranted.";
      if (avg < 0.15) return "Low red-edge reflectance suggests reduced chlorophyll concentration. This is an early indicator of nitrogen stress — often visible in NDRE before the crop shows visible yellowing.";
      if (avg < 0.3) return "Moderate chlorophyll content. Adequate for sustained growth but below peak levels. A variable-rate nitrogen application may improve uniformity across the field.";
      if (avg < 0.45) return "Good chlorophyll and nitrogen status. The crop has sufficient nutrient reserves for active growth. NDRE at this level typically correlates with strong yield potential.";
      return "High chlorophyll concentration and excellent nitrogen uptake. The crop is performing at or near its photosynthetic potential for this growth stage.";
    },
    trend: (first, second) => {
      const delta = second - first;
      if (delta > 0.05) return "Chlorophyll content is increasing — nitrogen uptake is active and the crop is building photosynthetic capacity.";
      if (delta < -0.05) return "Chlorophyll levels are dropping. This could indicate nitrogen depletion, natural senescence, or the onset of disease.";
      return "Chlorophyll levels have been stable — nitrogen supply appears adequate for current growth.";
    },
  },
  ndmi: {
    measures:
      "Ratio of near-infrared to short-wave infrared (SWIR) reflectance. SWIR is absorbed by leaf water content, so the difference reveals canopy moisture status at the cellular level.",
    farmContext:
      "Directly measures crop water stress before it becomes visible. A dropping NDMI often precedes wilting by several days, giving time to adjust irrigation. Also useful for monitoring drought impact across large areas.",
    bands: "B8 (NIR) & B11 (SWIR 1610 nm) — 20 m resolution",
    zones: [
      { label: "Dry / water stressed", min: -0.3, max: -0.1, tone: "critical" },
      { label: "Low moisture", min: -0.1, max: 0.0, tone: "caution" },
      { label: "Moderate moisture", min: 0.0, max: 0.15, tone: "neutral" },
      { label: "Adequate moisture", min: 0.15, max: 0.3, tone: "positive" },
      { label: "High canopy moisture", min: 0.3, max: 0.5, tone: "positive" },
    ],
    assess: (avg) => {
      if (avg < -0.1) return "Canopy moisture is critically low. The crop is likely experiencing water stress. If this area is irrigated, check for system failures. In dryland systems, this reflects drought conditions.";
      if (avg < 0.0) return "Below-average moisture content. Early water stress may be developing. This is the stage where irrigation scheduling adjustments can prevent yield loss.";
      if (avg < 0.15) return "Moderate canopy moisture — within acceptable range for many crops but not optimal. Monitor for decline, especially during hot or windy periods.";
      if (avg < 0.3) return "Good moisture levels. The crop has adequate water for normal physiological function. Transpiration and photosynthesis should be operating efficiently.";
      return "High canopy water content indicates excellent moisture availability. The crop is well-hydrated. In some cases, very high values could also indicate waterlogging — cross-reference with drainage conditions.";
    },
    trend: (first, second) => {
      const delta = second - first;
      if (delta > 0.06) return "Canopy moisture is increasing — recent rainfall or irrigation is being taken up effectively.";
      if (delta < -0.06) return "Moisture levels are declining. The crop is drying down — this could be natural dry-down near harvest or developing water stress.";
      return "Moisture content has remained stable through the observed period.";
    },
  },
};

function IndexInsights({ passes, index }: { passes: AOISeriesPass[]; index: IndexKey }) {
  const meta = indexMeta(index);
  const insight = INDEX_INSIGHTS[index];
  if (!insight) return null;

  // Compute average mean from usable passes
  const usable = passes.filter(
    (p) => (p.status === "ok" || p.status === "interpolated") && p.mean != null,
  );
  if (usable.length === 0) return null;

  const avg = usable.reduce((s, p) => s + (p.mean as number), 0) / usable.length;

  // Find which zone the average falls into
  const activeZone = insight.zones.find((z) => avg >= z.min && avg < z.max)
    ?? insight.zones[insight.zones.length - 1];

  // Trend: compare first half vs second half
  const mid = Math.floor(usable.length / 2);
  let trendMessage: string | null = null;
  if (usable.length >= 4) {
    const firstHalf = usable.slice(0, mid).reduce((s, p) => s + (p.mean as number), 0) / mid;
    const secondHalf = usable.slice(mid).reduce((s, p) => s + (p.mean as number), 0) / (usable.length - mid);
    trendMessage = insight.trend(firstHalf, secondHalf);
  }

  return (
    <div className="rounded-lg border border-border/60 bg-panel-2/50 p-3">
      {/* Section header */}
      <p className="mb-2.5 text-[10px] font-medium uppercase tracking-wider text-muted">
        {meta.label} Insights
        <span className="ml-1.5 font-normal normal-case text-muted/70">
          — agronomic interpretation
        </span>
      </p>

      <div className="flex flex-col gap-3">
        {/* What it measures */}
        <div>
          <p className="mb-0.5 text-[10px] font-semibold uppercase tracking-wide text-muted/80">
            What {meta.label} measures
          </p>
          <p className="text-xs leading-relaxed text-fg/85">
            {insight.measures}
          </p>
          <p className="mt-1 text-[11px] leading-relaxed text-muted">
            {insight.farmContext}
          </p>
          <p className="mt-1 text-[10px] font-mono text-muted/60">
            Bands: {insight.bands}
          </p>
        </div>

        {/* Value scale */}
        <div>
          <p className="mb-1.5 text-[10px] font-semibold uppercase tracking-wide text-muted/80">
            Value scale
          </p>
          <div className="flex flex-col gap-1">
            {insight.zones.map((zone) => {
              const isActive = zone === activeZone;
              return (
                <div
                  key={zone.label}
                  className={`flex items-center gap-2 rounded-md px-2 py-1 text-[11px] transition-colors ${
                    isActive
                      ? "bg-panel border border-border"
                      : "opacity-60"
                  }`}
                >
                  <span
                    className="size-2 shrink-0 rounded-full"
                    style={{
                      background:
                        zone.tone === "positive"
                          ? "var(--positive)"
                          : zone.tone === "caution"
                            ? "var(--caution)"
                            : zone.tone === "critical"
                              ? "var(--critical)"
                              : "var(--muted)",
                    }}
                  />
                  <span className="tabular-nums text-muted">
                    {zone.min.toFixed(2)} – {zone.max.toFixed(2)}
                  </span>
                  <span className={`${isActive ? "font-medium text-fg" : "text-muted"}`}>
                    {zone.label}
                  </span>
                  {isActive && (
                    <span className="ml-auto text-[9px] font-medium uppercase tracking-wide text-accent">
                      current avg
                    </span>
                  )}
                </div>
              );
            })}
          </div>
        </div>

        {/* Assessment */}
        <div className="rounded-md border border-border/50 bg-panel px-3 py-2">
          <p className="mb-1 text-[10px] font-semibold uppercase tracking-wide text-muted/80">
            Assessment
            <span className="ml-1.5 font-normal normal-case text-muted/50">
              — avg {meta.label}: {avg.toFixed(3)}
            </span>
          </p>
          <p className="text-xs leading-relaxed text-fg/90">
            {insight.assess(avg)}
          </p>
          {trendMessage && (
            <p className="mt-1.5 text-[11px] leading-relaxed text-muted">
              <span className="font-medium text-fg/70">Trend:</span>{" "}
              {trendMessage}
            </p>
          )}
        </div>
      </div>
    </div>
  );
}

/* ─── Stat Cards ──────────────────────────────────────────────────────────── */

function StatCards({
  highPoint,
  lowPoint,
  avgMean,
  totalPasses,
  index,
}: {
  highPoint: ChartPoint | null;
  lowPoint: ChartPoint | null;
  avgMean: number;
  totalPasses: number;
  index: IndexKey;
}) {
  const meta = indexMeta(index);

  return (
    <div className="grid grid-cols-2 gap-2 sm:grid-cols-4">
      <div className="stat-card flex flex-col gap-1 px-3 py-2.5">
        <span className="text-[10px] font-medium uppercase tracking-wider text-muted">
          Highest
        </span>
        <div className="flex items-center gap-2">
          <ArrowUp size={14} weight="bold" className="text-positive" />
          <span className="text-base font-semibold tabular-nums text-fg">
            {highPoint ? formatNumber(highPoint.mean) : "·"}
          </span>
          {highPoint && (
            <span
              className="size-2.5 shrink-0 rounded-full"
              style={{ background: colorForValue(meta, highPoint.mean) }}
            />
          )}
        </div>
        <span className="text-[10px] text-muted">
          {highPoint ? highPoint.dateLabel : ""}
        </span>
      </div>

      <div className="stat-card flex flex-col gap-1 px-3 py-2.5">
        <span className="text-[10px] font-medium uppercase tracking-wider text-muted">
          Lowest
        </span>
        <div className="flex items-center gap-2">
          <ArrowDown size={14} weight="bold" className="text-critical" />
          <span className="text-base font-semibold tabular-nums text-fg">
            {lowPoint ? formatNumber(lowPoint.mean) : "·"}
          </span>
          {lowPoint && (
            <span
              className="size-2.5 shrink-0 rounded-full"
              style={{ background: colorForValue(meta, lowPoint.mean) }}
            />
          )}
        </div>
        <span className="text-[10px] text-muted">
          {lowPoint ? lowPoint.dateLabel : ""}
        </span>
      </div>

      <div className="stat-card flex flex-col gap-1 px-3 py-2.5">
        <span className="text-[10px] font-medium uppercase tracking-wider text-muted">
          Average
        </span>
        <div className="flex items-center gap-2">
          <span className="text-base font-semibold tabular-nums text-fg">
            {formatNumber(avgMean)}
          </span>
          <span
            className="size-2.5 shrink-0 rounded-full"
            style={{ background: colorForValue(meta, avgMean) }}
          />
        </div>
        <span className="text-[10px] text-muted">across {totalPasses} passes</span>
      </div>

      <div className="stat-card flex flex-col gap-1 px-3 py-2.5">
        <span className="text-[10px] font-medium uppercase tracking-wider text-muted">
          Passes
        </span>
        <span className="text-base font-semibold tabular-nums text-fg">{totalPasses}</span>
        <span className="text-[10px] text-muted">{meta.long}</span>
      </div>
    </div>
  );
}

/* ─── Main Interactive Chart ──────────────────────────────────────────────── */

function CustomTooltip({ active, payload }: any) {
  if (!active || !payload || payload.length === 0) return null;
  const data = payload[0]?.payload as ChartPoint | undefined;
  if (!data) return null;

  return (
    <div className="chart-tooltip px-3 py-2.5">
      <p className="mb-1.5 text-xs font-medium text-fg">
        {data.dateLabel}
        {data.interpolated && (
          <Badge tone="caution" className="ml-1.5 text-[9px] uppercase">
            avg
          </Badge>
        )}
      </p>
      <div className="grid grid-cols-2 gap-x-4 gap-y-1 text-[11px]">
        <div className="flex items-center gap-1.5">
          <span
            className="size-2 shrink-0 rounded-full"
            style={{ background: data.color }}
          />
          <span className="text-muted">Mean</span>
        </div>
        <span className="text-right font-medium tabular-nums text-fg">
          {formatNumber(data.mean)}
        </span>

        <span className="text-muted">Min</span>
        <span className="text-right tabular-nums text-muted">
          {formatNumber(data.min)}
        </span>

        <span className="text-muted">Max</span>
        <span className="text-right tabular-nums text-muted">
          {formatNumber(data.max)}
        </span>

        <span className="text-muted">P10</span>
        <span className="text-right tabular-nums text-muted">
          {formatNumber(data.p10)}
        </span>

        <span className="text-muted">P90</span>
        <span className="text-right tabular-nums text-muted">
          {formatNumber(data.p90)}
        </span>

        {data.clearFraction != null && (
          <>
            <span className="text-muted">Clear</span>
            <span className="text-right tabular-nums text-muted">
              {formatPercent(data.clearFraction)}
            </span>
          </>
        )}

        {data.confidence && (
          <>
            <span className="text-muted">Conf.</span>
            <span className="text-right">
              <Badge
                tone={confidenceTone(data.confidence)}
                className="text-[9px] uppercase"
              >
                {data.confidence}
              </Badge>
            </span>
          </>
        )}
      </div>
    </div>
  );
}

function CustomDot(props: any) {
  const { cx, cy, payload } = props;
  if (!payload) return null;
  const pt = payload as ChartPoint;
  return (
    <circle
      cx={cx}
      cy={cy}
      r={pt.interpolated ? 3.5 : 4}
      fill={pt.interpolated ? "none" : pt.color}
      stroke={pt.color}
      strokeWidth={pt.interpolated ? 1.5 : 0}
      className="chart-dot"
      style={{ transition: "r 120ms ease-out, filter 120ms ease-out" }}
    />
  );
}

function CustomActiveDot(props: any) {
  const { cx, cy, payload } = props;
  if (!payload) return null;
  const pt = payload as ChartPoint;
  return (
    <circle
      cx={cx}
      cy={cy}
      r={6}
      fill={pt.color}
      stroke="var(--panel)"
      strokeWidth={2}
      className="chart-dot chart-dot--hover"
    />
  );
}

function MainChart({ points, index }: { points: ChartPoint[]; index: IndexKey }) {
  const meta = indexMeta(index);
  const tooltipContent = useCallback(
    (props: any) => <CustomTooltip {...props} index={index} />,
    [index],
  );

  return (
    <ResponsiveContainer width="100%" height={220}>
      <LineChart data={points} margin={{ top: 8, right: 12, bottom: 4, left: 0 }}>
        <defs>
          <linearGradient id="meanGradient" x1="0" y1="0" x2="1" y2="0">
            {meta.gradient.map((color, i) => (
              <stop
                key={i}
                offset={`${(i / (meta.gradient.length - 1)) * 100}%`}
                stopColor={color}
              />
            ))}
          </linearGradient>
        </defs>
        <CartesianGrid
          strokeDasharray="3 3"
          stroke="var(--border)"
          strokeOpacity={0.5}
          vertical={false}
        />
        <XAxis
          dataKey="dateLabel"
          tick={{ fontSize: 10, fill: "var(--muted)" }}
          tickLine={false}
          axisLine={{ stroke: "var(--border)" }}
          interval="preserveStartEnd"
          minTickGap={40}
        />
        <YAxis
          domain={[meta.min, meta.max]}
          tick={{ fontSize: 10, fill: "var(--muted)" }}
          tickLine={false}
          axisLine={false}
          tickFormatter={(v: number) => v.toFixed(1)}
          width={36}
        />
        <Tooltip
          content={tooltipContent}
          cursor={{
            stroke: "var(--accent)",
            strokeWidth: 1,
            strokeDasharray: "4 3",
            strokeOpacity: 0.55,
          }}
        />
        <ReferenceLine y={0} stroke="var(--border)" strokeWidth={1} />
        <Line
          type="monotone"
          dataKey="mean"
          stroke="url(#meanGradient)"
          strokeWidth={2}
          dot={<CustomDot />}
          activeDot={<CustomActiveDot />}
          animationDuration={600}
          animationEasing="ease-out"
        />
      </LineChart>
    </ResponsiveContainer>
  );
}

/* ─── Range Band Chart ────────────────────────────────────────────────────── */

function RangeChart({ points, index }: { points: ChartPoint[]; index: IndexKey }) {
  const meta = indexMeta(index);

  const rangeData = useMemo(
    () =>
      points.map((p) => ({
        ...p,
        minMax: [p.min ?? p.mean, p.max ?? p.mean] as [number, number],
        p10p90: [p.p10 ?? p.mean, p.p90 ?? p.mean] as [number, number],
      })),
    [points],
  );

  const tooltipContent = useCallback(
    (props: any) => <CustomTooltip {...props} index={index} />,
    [index],
  );

  return (
    <ResponsiveContainer width="100%" height={180}>
      <AreaChart data={rangeData} margin={{ top: 8, right: 12, bottom: 4, left: 0 }}>
        <defs>
          <linearGradient id="minMaxFill" x1="0" y1="0" x2="0" y2="1">
            <stop offset="0%" stopColor="var(--accent)" stopOpacity={0.08} />
            <stop offset="100%" stopColor="var(--accent)" stopOpacity={0.02} />
          </linearGradient>
          <linearGradient id="p10p90Fill" x1="0" y1="0" x2="0" y2="1">
            <stop offset="0%" stopColor="var(--accent)" stopOpacity={0.18} />
            <stop offset="100%" stopColor="var(--accent)" stopOpacity={0.06} />
          </linearGradient>
        </defs>
        <CartesianGrid
          strokeDasharray="3 3"
          stroke="var(--border)"
          strokeOpacity={0.5}
          vertical={false}
        />
        <XAxis
          dataKey="dateLabel"
          tick={{ fontSize: 10, fill: "var(--muted)" }}
          tickLine={false}
          axisLine={{ stroke: "var(--border)" }}
          interval="preserveStartEnd"
          minTickGap={40}
        />
        <YAxis
          domain={[meta.min, meta.max]}
          tick={{ fontSize: 10, fill: "var(--muted)" }}
          tickLine={false}
          axisLine={false}
          tickFormatter={(v: number) => v.toFixed(1)}
          width={36}
        />
        <Tooltip
          content={tooltipContent}
          cursor={{
            stroke: "var(--accent)",
            strokeWidth: 1,
            strokeDasharray: "4 3",
            strokeOpacity: 0.55,
          }}
        />

        {/* Min–Max outer range */}
        <Area
          type="monotone"
          dataKey="minMax"
          stroke="none"
          fill="url(#minMaxFill)"
          animationDuration={600}
        />

        {/* P10–P90 inner range */}
        <Area
          type="monotone"
          dataKey="p10p90"
          stroke="var(--accent)"
          strokeWidth={1}
          strokeOpacity={0.3}
          fill="url(#p10p90Fill)"
          animationDuration={600}
        />

        {/* Mean line on top */}
        <Area
          type="monotone"
          dataKey="mean"
          stroke="var(--accent)"
          strokeWidth={2}
          fill="none"
          dot={<CustomDot />}
          activeDot={<CustomActiveDot />}
          animationDuration={600}
        />
      </AreaChart>
    </ResponsiveContainer>
  );
}

/* ─── Temporal Heatmap Strip ──────────────────────────────────────────────── */

function HeatmapStrip({ points, index }: { points: ChartPoint[]; index: IndexKey }) {
  const meta = indexMeta(index);
  const [hoveredIdx, setHoveredIdx] = useState<number | null>(null);

  return (
    <div className="flex flex-col gap-2">
      {/* Color strip */}
      <div className="flex h-10 overflow-hidden rounded-md border border-border">
        {points.map((p, i) => (
          <div
            key={p.date}
            className="relative flex-1 transition-all duration-150"
            style={{
              background: p.color,
              opacity: hoveredIdx !== null && hoveredIdx !== i ? 0.45 : 1,
              transform: hoveredIdx === i ? "scaleY(1.15)" : "scaleY(1)",
            }}
            onMouseEnter={() => setHoveredIdx(i)}
            onMouseLeave={() => setHoveredIdx(null)}
            title={`${p.dateLabel}: ${formatNumber(p.mean)}`}
          />
        ))}
      </div>

      {/* Hovered detail */}
      <div className="flex items-center justify-between text-[10px] text-muted">
        {hoveredIdx !== null ? (
          <>
            <span className="font-medium text-fg">{points[hoveredIdx].dateLabel}</span>
            <span className="flex items-center gap-1.5">
              <span
                className="size-2 rounded-full"
                style={{ background: points[hoveredIdx].color }}
              />
              <span className="tabular-nums font-medium text-fg">
                {formatNumber(points[hoveredIdx].mean)}
              </span>
              {points[hoveredIdx].confidence && (
                <Badge
                  tone={confidenceTone(points[hoveredIdx].confidence!)}
                  className="text-[8px] uppercase"
                >
                  {points[hoveredIdx].confidence}
                </Badge>
              )}
            </span>
          </>
        ) : (
          <>
            <span>{points[0].dateLabel}</span>
            <span>← hover to inspect →</span>
            <span>{points[points.length - 1].dateLabel}</span>
          </>
        )}
      </div>

      {/* Gradient legend */}
      <div className="flex items-center gap-2">
        <span className="text-[9px] tabular-nums text-muted">{meta.min.toFixed(1)}</span>
        <div
          className="h-2 flex-1 rounded-full"
          style={{ background: gradientCss(meta) }}
        />
        <span className="text-[9px] tabular-nums text-muted">{meta.max.toFixed(1)}</span>
      </div>
    </div>
  );
}

/* ─── Table View (original) ───────────────────────────────────────────────── */

function TableView({
  passes,
  index,
  hasRequested,
}: {
  passes: AOISeriesPass[];
  index: IndexKey;
  hasRequested: boolean;
}) {
  return (
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
              index={index}
              showRequested={hasRequested}
            />
          ))}
        </tbody>
      </table>
    </div>
  );
}

/* ─── Shared helpers ──────────────────────────────────────────────────────── */

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

export function confidenceTone(confidence: string): "positive" | "caution" | "critical" | "neutral" {
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
 *  (averaged) passes are hollow circles so the analyst can distinguish synthesised data. Reused by
 *  the report panel, so it is exported. */
export function MeanSparkline({ passes, index }: { passes: AOISeriesPass[]; index: IndexKey }) {
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
