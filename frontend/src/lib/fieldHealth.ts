/* Cross-index field-health analytics, shared by the AOI Studio results Overview and the analyst
 * workspace field inspector. Pure data logic (no JSX) so both surfaces feed the same component from
 * different data sources: AOI Studio passes a completed job map, the workspace passes per-index
 * timeseries reads. Both normalise into `OverviewSeries` here.
 *
 * Scoring ranges are kept in lockstep with lib/indices.ts (and rs_analysis/colormaps.py). The
 * thresholds are the provisional v1 set (engineering-approved, agronomist sign-off still outstanding
 * per docs/backlog/0015) - identical to what the Overview already used, just centralised. */

import type { AOIJob, TimeseriesPoint } from "./api";
import type { IndexKey } from "./indices";
import { colorForValue, indexMeta } from "./indices";

/* ─── Normalised series ───────────────────────────────────────────────────── */

export interface SeriesPoint {
  date: string;
  mean: number;
  min: number | null;
  max: number | null;
  p10: number | null;
  p90: number | null;
  clearFraction: number;
}

export interface IndexSeries {
  key: IndexKey;
  points: SeriesPoint[]; // chronological, mean present
}

export type OverviewSeries = Partial<Record<IndexKey, IndexSeries>>;

export const INDEX_KEYS: IndexKey[] = ["ndvi", "evi2", "savi", "ndre", "ndmi"];

const byDate = (a: SeriesPoint, b: SeriesPoint) => a.date.localeCompare(b.date);

/** Adapt a completed AOI Studio job map (one job per index) into the shared series shape. Only
 *  resolved passes with a real mean contribute; "no_pass"/"error" passes are dropped. */
export function seriesFromJobs(jobs: Record<IndexKey, AOIJob | undefined>): OverviewSeries {
  const out: OverviewSeries = {};
  for (const key of INDEX_KEYS) {
    const job = jobs[key];
    if (job?.state !== "done") continue;
    const points = (job.result?.passes ?? [])
      .filter(
        (p) =>
          (p.status === "ok" || p.status === "interpolated") &&
          p.mean != null &&
          !!p.pass_date,
      )
      .map<SeriesPoint>((p) => ({
        date: p.pass_date as string,
        mean: p.mean as number,
        min: p.min ?? null,
        max: p.max ?? null,
        p10: p.p10 ?? null,
        p90: p.p90 ?? null,
        clearFraction: p.clear_fraction ?? 0,
      }))
      .sort(byDate);
    if (points.length) out[key] = { key, points };
  }
  return out;
}

/** Adapt the workspace's per-index timeseries reads into the shared series shape. */
export function seriesFromTimeseries(
  byIndex: Partial<Record<IndexKey, TimeseriesPoint[]>>,
): OverviewSeries {
  const out: OverviewSeries = {};
  for (const key of INDEX_KEYS) {
    const arr = byIndex[key];
    if (!arr) continue;
    const points = arr
      .filter((p) => p.mean != null && !!p.pass_date)
      .map<SeriesPoint>((p) => ({
        date: p.pass_date,
        mean: p.mean as number,
        min: p.min,
        max: p.max,
        p10: p.p10,
        p90: p.p90,
        clearFraction: p.clear_fraction,
      }))
      .sort(byDate);
    if (points.length) out[key] = { key, points };
  }
  return out;
}

/* ─── Per-series statistics ───────────────────────────────────────────────── */

export type Tone = "positive" | "caution" | "critical" | "neutral";
export type TrendDir = "up" | "down" | "flat";

export interface Trend {
  delta: number;
  dir: TrendDir;
}

export function seriesAverage(s: IndexSeries): number {
  return s.points.reduce((sum, p) => sum + p.mean, 0) / s.points.length;
}

export function seriesLatest(s: IndexSeries): SeriesPoint {
  return s.points[s.points.length - 1];
}

/** First-to-latest change over the window. `dir` treats a small band as flat so tiny sampling
 *  wobble does not read as a trend. A single pass has no trend. */
export function seriesTrend(s: IndexSeries): Trend {
  if (s.points.length < 2) return { delta: 0, dir: "flat" };
  const delta = s.points[s.points.length - 1].mean - s.points[0].mean;
  const dir: TrendDir = Math.abs(delta) < 0.02 ? "flat" : delta > 0 ? "up" : "down";
  return { delta, dir };
}

/** Latest-pass spatial spread (p90 - p10) as a fraction of the index's display range: 0 = perfectly
 *  uniform, 1 = spans the whole scale. A proxy for within-AOI patchiness. Null when percentiles are
 *  absent. */
export function latestSpread(s: IndexSeries): number | null {
  const latest = seriesLatest(s);
  if (latest.p10 == null || latest.p90 == null) return null;
  const meta = indexMeta(s.key);
  const range = meta.max - meta.min || 1;
  return Math.min(1, Math.max(0, (latest.p90 - latest.p10) / range));
}

export function averages(series: OverviewSeries): Partial<Record<IndexKey, number>> {
  const out: Partial<Record<IndexKey, number>> = {};
  for (const key of INDEX_KEYS) {
    const s = series[key];
    if (s) out[key] = seriesAverage(s);
  }
  return out;
}

/* ─── Run summary ─────────────────────────────────────────────────────────── */

export interface OverviewSummary {
  indexCount: number;
  passCount: number; // clear passes on the deepest index
  dateStart: string | null;
  dateEnd: string | null;
  meanClearFraction: number | null;
}

export function summarise(series: OverviewSeries): OverviewSummary {
  const all = INDEX_KEYS.map((k) => series[k]).filter((s): s is IndexSeries => !!s);
  if (all.length === 0) {
    return {
      indexCount: 0,
      passCount: 0,
      dateStart: null,
      dateEnd: null,
      meanClearFraction: null,
    };
  }
  const deepest = all.reduce((a, b) => (b.points.length > a.points.length ? b : a));
  const allDates = all.flatMap((s) => s.points.map((p) => p.date));
  const allClear = all.flatMap((s) => s.points.map((p) => p.clearFraction));
  return {
    indexCount: all.length,
    passCount: deepest.points.length,
    dateStart: allDates.reduce((a, b) => (b < a ? b : a)),
    dateEnd: allDates.reduce((a, b) => (b > a ? b : a)),
    meanClearFraction: allClear.reduce((s, v) => s + v, 0) / allClear.length,
  };
}

/* ─── Scoring ─────────────────────────────────────────────────────────────── */

function normalise(v: number, min: number, max: number): number {
  return Math.min(100, Math.max(0, ((v - min) / (max - min)) * 100));
}

export function scoreToTone(s: number): Tone {
  if (s >= 65) return "positive";
  if (s >= 40) return "caution";
  if (s >= 15) return "critical";
  return "neutral";
}

export function scoreToLabel(s: number): string {
  if (s >= 80) return "Excellent";
  if (s >= 65) return "Good";
  if (s >= 45) return "Moderate";
  if (s >= 25) return "Stressed";
  return "Poor";
}

function weightedMean(pairs: [number, number][]): number {
  const totalW = pairs.reduce((s, [, w]) => s + w, 0);
  return pairs.reduce((s, [v, w]) => s + v * w, 0) / totalW;
}

export interface HealthDimension {
  key: "canopy" | "nitrogen" | "moisture" | "coverage";
  label: string;
  subtitle: string;
  indices: string[];
  primaryKey: IndexKey; // index whose series drives the sparkline / trend / spread
  score: number; // 0-100
  tone: Tone;
  statusLabel: string;
  value: number; // primary index average
}

export function buildDimensions(series: OverviewSeries): HealthDimension[] {
  const avgs = averages(series);
  const dims: HealthDimension[] = [];
  const { ndvi, evi2, ndre, ndmi, savi } = avgs;

  // Canopy vigour - NDVI (primary) + EVI2 (secondary, down-weighted: same bands)
  if (ndvi != null || evi2 != null) {
    const pairs: [number, number][] = [];
    if (ndvi != null) pairs.push([normalise(ndvi, -0.2, 0.9), 0.6]);
    if (evi2 != null) pairs.push([normalise(evi2, -0.1, 0.8), 0.4]);
    const score = weightedMean(pairs);
    dims.push({
      key: "canopy",
      label: "Canopy Vigour",
      subtitle: "Greenness & biomass",
      indices: [ndvi != null ? "NDVI" : null, evi2 != null ? "EVI2" : null].filter(
        Boolean,
      ) as string[],
      primaryKey: ndvi != null ? "ndvi" : "evi2",
      score,
      tone: scoreToTone(score),
      statusLabel: scoreToLabel(score),
      value: ndvi ?? evi2 ?? 0,
    });
  }

  if (ndre != null) {
    const score = normalise(ndre, -0.1, 0.6);
    dims.push({
      key: "nitrogen",
      label: "Nitrogen / Chl.",
      subtitle: "Red-edge chlorophyll",
      indices: ["NDRE"],
      primaryKey: "ndre",
      score,
      tone: scoreToTone(score),
      statusLabel: scoreToLabel(score),
      value: ndre,
    });
  }

  if (ndmi != null) {
    const score = normalise(ndmi, -0.3, 0.5);
    dims.push({
      key: "moisture",
      label: "Canopy Moisture",
      subtitle: "Water content (SWIR)",
      indices: ["NDMI"],
      primaryKey: "ndmi",
      score,
      tone: scoreToTone(score),
      statusLabel: scoreToLabel(score),
      value: ndmi,
    });
  }

  if (savi != null) {
    const score = normalise(savi, -0.1, 0.7);
    dims.push({
      key: "coverage",
      label: "Ground Coverage",
      subtitle: "Soil-adjusted canopy",
      indices: ["SAVI"],
      primaryKey: "savi",
      score,
      tone: scoreToTone(score),
      statusLabel: scoreToLabel(score),
      value: savi,
    });
  }

  return dims;
}

const DIMENSION_WEIGHTS: Record<HealthDimension["key"], number> = {
  canopy: 35,
  nitrogen: 25,
  moisture: 25,
  coverage: 15,
};

export function computeOverall(dims: HealthDimension[]): number {
  if (dims.length === 0) return 0;
  let totalW = 0;
  let sum = 0;
  for (const d of dims) {
    const w = DIMENSION_WEIGHTS[d.key] ?? 20;
    sum += d.score * w;
    totalW += w;
  }
  return Math.round(sum / totalW);
}

/* ─── Cross-index correlation findings ────────────────────────────────────── */

export interface CorrelationFinding {
  tone: Tone;
  text: string;
}

function fmt(n: number): string {
  return n.toFixed(3);
}

/** Static cross-index rules over the window averages, plus trend rules when a real series (>=3
 *  passes) is present. The static rules mirror the agronomic reasoning the Overview already shipped;
 *  the trend rules are additive and only fire on a genuine time series. */
export function buildCorrelations(series: OverviewSeries): CorrelationFinding[] {
  const avgs = averages(series);
  const findings: CorrelationFinding[] = [];
  const { ndvi, evi2, ndre, ndmi, savi } = avgs;

  if (ndvi != null && evi2 != null && ndvi < 0.1 && evi2 < 0.1) {
    findings.push({
      tone: "critical",
      text: `No meaningful vegetation detected across any index (NDVI ${fmt(ndvi)}, EVI2 ${fmt(evi2)}). The area is likely fallow, recently harvested, or experiencing complete crop failure.`,
    });
    return findings;
  }

  if (ndvi != null && evi2 != null && ndvi > 0.65 && evi2 > 0.45) {
    findings.push({
      tone: "positive",
      text: `Both NDVI (${fmt(ndvi)}) and EVI2 (${fmt(evi2)}) are elevated and tracking proportionally, confirming a genuinely dense, vigorous canopy rather than NDVI saturation. This is characteristic of peak-season growth in high-biomass crops.`,
    });
  }

  if (ndvi != null && ndre != null && ndvi > 0.45 && ndre < 0.22) {
    findings.push({
      tone: "caution",
      text: `Nitrogen deficiency risk: the canopy appears reasonably green (NDVI ${fmt(ndvi)}) but red-edge chlorophyll (NDRE ${fmt(ndre)}) is below the threshold for healthy N status. The crop may be masking early deficiency. Consider targeted N application before visible yellowing appears.`,
    });
  }

  if (ndre != null && ndvi != null && ndre > 0.32 && ndvi > 0.45) {
    findings.push({
      tone: "positive",
      text: `Strong chlorophyll concentration (NDRE ${fmt(ndre)}) alongside healthy canopy vigour (NDVI ${fmt(ndvi)}) confirms active nitrogen uptake and efficient photosynthetic capacity.`,
    });
  }

  if (ndvi != null && ndmi != null && ndvi > 0.4 && ndmi < 0.0) {
    findings.push({
      tone: "caution",
      text: `Moisture stress developing: canopy greenness is maintained (NDVI ${fmt(ndvi)}) but NDMI (${fmt(ndmi)}) is below neutral. The crop may be drawing down reserves. Water stress symptoms could emerge within days if not addressed.`,
    });
  }

  if (ndmi != null && ndmi < -0.15) {
    findings.push({
      tone: "critical",
      text: `Severe water stress: canopy moisture is critically low (NDMI ${fmt(ndmi)}). Immediate irrigation response is recommended if conditions allow. Cross-check with recent rainfall records before intervening.`,
    });
  }

  if (ndmi != null && ndmi > 0.18 && ndvi != null && ndvi > 0.35) {
    findings.push({
      tone: "positive",
      text: `Canopy moisture is adequate (NDMI ${fmt(ndmi)}) and vegetation is healthy. The crop is transpiring efficiently, with no irrigation stress signals detected.`,
    });
  }

  if (ndvi != null && savi != null && ndvi > 0.35 && savi < ndvi * 0.58) {
    findings.push({
      tone: "caution",
      text: `Partial canopy coverage: SAVI (${fmt(savi)}) is proportionally lower than NDVI (${fmt(ndvi)}), indicating visible soil between crop rows or non-uniform stand establishment. The canopy has not yet fully closed.`,
    });
  }

  if (ndmi != null && ndvi != null && ndmi > 0.28 && ndvi < 0.3) {
    findings.push({
      tone: "caution",
      text: `High canopy moisture (NDMI ${fmt(ndmi)}) but poor vegetation signal (NDVI ${fmt(ndvi)}): possible waterlogging or flooded conditions. Drainage status should be checked on the ground.`,
    });
  }

  // Trend rules: only on a genuine time series (>=3 passes), additive to the static picture.
  const ndviS = series.ndvi;
  const ndmiS = series.ndmi;
  if (ndviS && ndviS.points.length >= 3) {
    const t = seriesTrend(ndviS);
    if (t.dir === "down" && t.delta < -0.08) {
      findings.push({
        tone: "caution",
        text: `Declining vigour trend: NDVI has fallen ${fmt(Math.abs(t.delta))} across the window. A sustained downward canopy trajectory can precede senescence, pest pressure, or nutrient run-down. Worth a ground check.`,
      });
    } else if (t.dir === "up" && t.delta > 0.08) {
      findings.push({
        tone: "positive",
        text: `Improving vigour trend: NDVI has risen ${fmt(t.delta)} across the window, consistent with healthy canopy development through the growth stage.`,
      });
    }
  }
  if (ndmiS && ndmiS.points.length >= 3) {
    const t = seriesTrend(ndmiS);
    if (t.dir === "down" && t.delta < -0.06) {
      findings.push({
        tone: "critical",
        text: `Drying trend: canopy moisture (NDMI) has dropped ${fmt(Math.abs(t.delta))} over the window. If the decline continues without rainfall, plan an irrigation response ahead of visible wilting.`,
      });
    }
  }

  if (findings.length === 0 && ndvi != null && ndvi > 0.35) {
    findings.push({
      tone: "positive",
      text: `No concerning cross-index patterns detected. Index values are internally consistent and within healthy ranges for the growth stage indicated by the timeline.`,
    });
  }

  return findings;
}

/* ─── Chart helpers ───────────────────────────────────────────────────────── */

/** A representative line colour for an index: its ramp sampled at that index's window average, so
 *  the multi-index chart legend colour matches how the value reads on the map. */
export function indexLineColor(key: IndexKey, avg: number): string {
  return colorForValue(indexMeta(key), avg);
}
