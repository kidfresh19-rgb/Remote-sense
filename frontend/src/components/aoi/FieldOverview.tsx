import { useState } from "react";
import { CaretDown, CaretRight, CheckCircle } from "@phosphor-icons/react";

import type { AOIJob } from "@/lib/api";
import { formatNumber } from "@/lib/format";
import type { IndexKey } from "@/lib/indices";

/* ─── Types ───────────────────────────────────────────────────────────────── */

interface HealthDimension {
  key: string;
  label: string;
  subtitle: string;
  indices: string[];
  score: number; // 0–100
  tone: "positive" | "caution" | "critical" | "neutral";
  statusLabel: string;
  value: string;
}

interface CorrelationFinding {
  tone: "positive" | "caution" | "critical" | "neutral";
  text: string;
}

/* ─── CDSE Validation Matrix (pinned against Copernicus Browser Process API, 2026-06-04) ── */

type CDSERow = { ndvi: number; evi2: number; savi: number; ndre: number; ndmi: number };
const CDSE_INDICES: (keyof CDSERow)[] = ["ndvi", "evi2", "savi", "ndre", "ndmi"];

const CDSE_MATRIX: { label: string; scene: string; rs: CDSERow; browser: CDSERow }[] = [
  {
    label: "Harare cropland",
    scene: "S2A · 2026-05-17 · T36KTF",
    rs: { ndvi: 0.3800, evi2: 0.1930, savi: 0.2040, ndre: 0.2418, ndmi: -0.0179 },
    browser: { ndvi: 0.3856, evi2: 0.1938, savi: 0.2049, ndre: 0.2460, ndmi: -0.0152 },
  },
  {
    label: "Mazowe Valley",
    scene: "S2A · 2026-05-17 · T36KTF",
    rs: { ndvi: 0.3804, evi2: 0.1831, savi: 0.1967, ndre: 0.2190, ndmi: -0.1469 },
    browser: { ndvi: 0.3815, evi2: 0.1832, savi: 0.1969, ndre: 0.2199, ndmi: -0.1463 },
  },
  {
    label: "Harare cropland",
    scene: "S2C · 2026-05-05 · T36KTF",
    rs: { ndvi: 0.4076, evi2: 0.2072, savi: 0.2182, ndre: 0.2481, ndmi: -0.0082 },
    browser: { ndvi: 0.4083, evi2: 0.2079, savi: 0.2189, ndre: 0.2488, ndmi: -0.0076 },
  },
];

/* ─── Scoring helpers ─────────────────────────────────────────────────────── */

function normalise(v: number, min: number, max: number): number {
  return Math.min(100, Math.max(0, ((v - min) / (max - min)) * 100));
}

function scoreToTone(s: number): "positive" | "caution" | "critical" | "neutral" {
  if (s >= 65) return "positive";
  if (s >= 40) return "caution";
  if (s >= 15) return "critical";
  return "neutral";
}

function scoreToLabel(s: number): string {
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

/* ─── Dimension builder ───────────────────────────────────────────────────── */

function buildDimensions(avgs: Partial<Record<IndexKey, number>>): HealthDimension[] {
  const dims: HealthDimension[] = [];

  // Canopy vigour — NDVI (primary) + EVI2 (secondary, down-weighted because same bands)
  const ndvi = avgs.ndvi;
  const evi2 = avgs.evi2;
  if (ndvi != null || evi2 != null) {
    const pairs: [number, number][] = [];
    if (ndvi != null) pairs.push([normalise(ndvi, -0.2, 0.9), 0.6]);
    if (evi2 != null) pairs.push([normalise(evi2, -0.1, 0.8), 0.4]);
    const score = weightedMean(pairs);
    const tone = scoreToTone(score);
    const primaryVal = ndvi ?? evi2 ?? 0;
    dims.push({
      key: "canopy",
      label: "Canopy Vigour",
      subtitle: "Greenness & biomass",
      indices: [ndvi != null ? "NDVI" : null, evi2 != null ? "EVI2" : null].filter(Boolean) as string[],
      score,
      tone,
      statusLabel: scoreToLabel(score),
      value: formatNumber(primaryVal),
    });
  }

  // Nitrogen / Chlorophyll — NDRE
  const ndre = avgs.ndre;
  if (ndre != null) {
    const score = normalise(ndre, -0.1, 0.6);
    const tone = scoreToTone(score);
    dims.push({
      key: "nitrogen",
      label: "Nitrogen / Chl.",
      subtitle: "Red-edge chlorophyll",
      indices: ["NDRE"],
      score,
      tone,
      statusLabel: scoreToLabel(score),
      value: formatNumber(ndre),
    });
  }

  // Canopy moisture — NDMI
  const ndmi = avgs.ndmi;
  if (ndmi != null) {
    const score = normalise(ndmi, -0.3, 0.5);
    const tone = scoreToTone(score);
    dims.push({
      key: "moisture",
      label: "Canopy Moisture",
      subtitle: "Water content (SWIR)",
      indices: ["NDMI"],
      score,
      tone,
      statusLabel: scoreToLabel(score),
      value: formatNumber(ndmi),
    });
  }

  // Ground coverage — SAVI
  const savi = avgs.savi;
  if (savi != null) {
    const score = normalise(savi, -0.1, 0.7);
    const tone = scoreToTone(score);
    dims.push({
      key: "coverage",
      label: "Ground Coverage",
      subtitle: "Soil-adjusted canopy",
      indices: ["SAVI"],
      score,
      tone,
      statusLabel: scoreToLabel(score),
      value: formatNumber(savi),
    });
  }

  return dims;
}

/* ─── Overall score ───────────────────────────────────────────────────────── */

function computeOverall(dims: HealthDimension[]): number {
  if (dims.length === 0) return 0;
  const weights: Record<string, number> = {
    canopy: 35,
    nitrogen: 25,
    moisture: 25,
    coverage: 15,
  };
  let totalW = 0;
  let sum = 0;
  for (const d of dims) {
    const w = weights[d.key] ?? 20;
    sum += d.score * w;
    totalW += w;
  }
  return Math.round(sum / totalW);
}

/* ─── Cross-index correlation findings ───────────────────────────────────── */

function buildCorrelations(avgs: Partial<Record<IndexKey, number>>): CorrelationFinding[] {
  const findings: CorrelationFinding[] = [];
  const { ndvi, evi2, ndre, ndmi, savi } = avgs;

  // No vegetation at all
  if (ndvi != null && evi2 != null && ndvi < 0.1 && evi2 < 0.1) {
    findings.push({
      tone: "critical",
      text: `No meaningful vegetation detected across any index (NDVI ${formatNumber(ndvi)}, EVI2 ${formatNumber(evi2)}). The area is likely fallow, recently harvested, or experiencing complete crop failure.`,
    });
    return findings; // no point adding more
  }

  // Dense confirmed canopy (EVI2 high relative to NDVI — not saturating)
  if (ndvi != null && evi2 != null && ndvi > 0.65 && evi2 > 0.45) {
    findings.push({
      tone: "positive",
      text: `Both NDVI (${formatNumber(ndvi)}) and EVI2 (${formatNumber(evi2)}) are elevated and tracking proportionally, confirming a genuinely dense, vigorous canopy rather than NDVI saturation. This is characteristic of peak-season growth in high-biomass crops.`,
    });
  }

  // Hidden N stress: green canopy but low NDRE
  if (ndvi != null && ndre != null && ndvi > 0.45 && ndre < 0.22) {
    findings.push({
      tone: "caution",
      text: `Nitrogen deficiency risk: the canopy appears reasonably green (NDVI ${formatNumber(ndvi)}) but red-edge chlorophyll (NDRE ${formatNumber(ndre)}) is below the threshold for healthy N status. The crop may be masking early deficiency — consider targeted N application before visible yellowing appears.`,
    });
  }

  // Good N uptake confirmed by NDRE
  if (ndre != null && ndvi != null && ndre > 0.32 && ndvi > 0.45) {
    findings.push({
      tone: "positive",
      text: `Strong chlorophyll concentration (NDRE ${formatNumber(ndre)}) alongside healthy canopy vigour (NDVI ${formatNumber(ndvi)}) confirms active nitrogen uptake and efficient photosynthetic capacity.`,
    });
  }

  // Water stress under green canopy
  if (ndvi != null && ndmi != null && ndvi > 0.4 && ndmi < 0.0) {
    findings.push({
      tone: "caution",
      text: `Moisture stress developing: canopy greenness is maintained (NDVI ${formatNumber(ndvi)}) but NDMI (${formatNumber(ndmi)}) is below neutral. The crop may be drawing down reserves — water stress symptoms could emerge within days if not addressed.`,
    });
  }

  // Critical water stress
  if (ndmi != null && ndmi < -0.15) {
    findings.push({
      tone: "critical",
      text: `Severe water stress: canopy moisture is critically low (NDMI ${formatNumber(ndmi)}). Immediate irrigation response is recommended if conditions allow. Cross-check with recent rainfall records before intervening.`,
    });
  }

  // Well-hydrated crop
  if (ndmi != null && ndmi > 0.18 && ndvi != null && ndvi > 0.35) {
    findings.push({
      tone: "positive",
      text: `Canopy moisture is adequate (NDMI ${formatNumber(ndmi)}) and vegetation is healthy — the crop is transpiring efficiently. No irrigation stress signals detected.`,
    });
  }

  // Patchy canopy: SAVI much lower than NDVI implies
  if (ndvi != null && savi != null && ndvi > 0.35 && savi < ndvi * 0.58) {
    findings.push({
      tone: "caution",
      text: `Partial canopy coverage: SAVI (${formatNumber(savi)}) is proportionally lower than NDVI (${formatNumber(ndvi)}), indicating visible soil between crop rows or non-uniform stand establishment. The canopy has not yet fully closed.`,
    });
  }

  // Possible waterlogging: high moisture but poor vegetation
  if (ndmi != null && ndvi != null && ndmi > 0.28 && ndvi < 0.3) {
    findings.push({
      tone: "caution",
      text: `High canopy moisture (NDMI ${formatNumber(ndmi)}) but poor vegetation signal (NDVI ${formatNumber(ndvi)}): possible waterlogging or flooded conditions. Drainage status should be checked on the ground.`,
    });
  }

  // Healthy baseline — all good, no concerning correlations found
  if (findings.length === 0 && ndvi != null && ndvi > 0.35) {
    findings.push({
      tone: "positive",
      text: `No concerning cross-index patterns detected. Index values are internally consistent and within healthy ranges for the growth stage indicated by the timeline.`,
    });
  }

  return findings;
}

/* ─── CDSE Calibration panel ─────────────────────────────────────────────── */

function CDSECalibrationPanel() {
  const [open, setOpen] = useState(false);

  return (
    <div className="rounded-lg border border-border/50 bg-panel-2/40 overflow-hidden">
      <button
        onClick={() => setOpen((v) => !v)}
        className="flex w-full items-center gap-2 px-3 py-2 text-left text-[11px] text-muted transition-colors hover:text-fg"
      >
        <span className="flex items-center gap-1.5 text-positive">
          <CheckCircle size={13} weight="fill" />
          <span className="font-medium text-fg">CDSE validated</span>
        </span>
        <span className="flex-1 text-muted">
          Values calibrated against Copernicus Browser within ±0.01
        </span>
        {open ? <CaretDown size={11} /> : <CaretRight size={11} />}
      </button>

      {open && (
        <div className="border-t border-border/50 px-3 pb-3">
          <p className="mb-2 mt-2.5 text-[10px] leading-relaxed text-muted">
            Engine values were validated against the Copernicus Browser Process API (SCL mask{" "}
            <code className="rounded bg-panel px-0.5 font-mono text-[9px]">{"{4,5,6,7}"}</code>, 20 m
            output, AOI-mean) on three pinned Zimbabwe scenes. All five indices agreed within ±0.01.
          </p>

          <div className="overflow-x-auto">
            <table className="w-full border-collapse text-[10px]">
              <thead>
                <tr className="border-b border-border text-left text-muted">
                  <th className="py-1.5 pr-3 font-medium min-w-[110px]">Scene</th>
                  <th className="py-1.5 pr-2 text-right font-medium">NDVI</th>
                  <th className="py-1.5 pr-2 text-right font-medium">EVI2</th>
                  <th className="py-1.5 pr-2 text-right font-medium">SAVI</th>
                  <th className="py-1.5 pr-2 text-right font-medium">NDRE</th>
                  <th className="py-1.5 text-right font-medium">NDMI</th>
                </tr>
              </thead>
              <tbody>
                {CDSE_MATRIX.map((row, i) => {
                  const deltas = CDSE_INDICES.map(
                    (k) => row.browser[k] - row.rs[k],
                  );
                  return (
                    <tr
                      key={i}
                      className="border-b border-border/40"
                    >
                      <td className="py-1.5 pr-3 align-top">
                        <p className="font-medium text-fg">{row.label}</p>
                        <p className="text-[9px] text-muted font-mono leading-tight">{row.scene}</p>
                        <p className="mt-0.5 text-[9px] text-muted">remote-sense</p>
                        <p className="text-[9px] text-muted">Copernicus</p>
                        <p className="text-[9px] text-muted">delta</p>
                      </td>
                      {CDSE_INDICES.map((k, ki) => {
                        const d = deltas[ki];
                        const absd = Math.abs(d);
                        return (
                          <td key={k} className="py-1.5 pr-2 text-right align-top tabular-nums">
                            <p className="h-[1.1rem]" />
                            <p className="h-[.85rem]" />
                            <p className="text-fg">{row.rs[k].toFixed(3)}</p>
                            <p className="text-muted">{row.browser[k].toFixed(3)}</p>
                            <p
                              className={
                                absd <= 0.005
                                  ? "text-positive"
                                  : absd <= 0.01
                                    ? "text-caution"
                                    : "text-critical"
                              }
                            >
                              {d >= 0 ? "+" : ""}
                              {d.toFixed(3)}
                            </p>
                          </td>
                        );
                      })}
                    </tr>
                  );
                })}
              </tbody>
            </table>
          </div>

          <p className="mt-2 text-[9px] leading-relaxed text-muted/70">
            Residual arises from UTM windowed read vs geographic Process render, not from index
            formula differences. Four structural differences (percentile bins, SCL mask set, AOI
            polygon source, mosaicking order) are documented in{" "}
            <code className="font-mono text-[8px]">test_validation_matrix_live.py</code>.
          </p>
        </div>
      )}
    </div>
  );
}

/* ─── Dimension card ──────────────────────────────────────────────────────── */

const TONE_BAR: Record<HealthDimension["tone"], string> = {
  positive: "bg-positive",
  caution: "bg-caution",
  critical: "bg-critical",
  neutral: "bg-muted",
};

const TONE_TEXT: Record<HealthDimension["tone"], string> = {
  positive: "text-positive",
  caution: "text-caution",
  critical: "text-critical",
  neutral: "text-muted",
};

function DimensionCard({ dim }: { dim: HealthDimension }) {
  return (
    <div className="flex flex-col gap-1.5 rounded-lg border border-border bg-panel p-3">
      <div className="flex items-start justify-between gap-1">
        <div>
          <p className="text-[10px] font-semibold text-fg leading-tight">{dim.label}</p>
          <p className="text-[9px] text-muted leading-tight">{dim.subtitle}</p>
        </div>
        <span className={`text-[10px] font-medium ${TONE_TEXT[dim.tone]}`}>
          {dim.statusLabel}
        </span>
      </div>

      {/* Score bar */}
      <div className="h-1.5 w-full overflow-hidden rounded-full bg-panel-2">
        <div
          className={`h-full rounded-full transition-[width] duration-500 ease-out ${TONE_BAR[dim.tone]}`}
          style={{ width: `${dim.score}%` }}
        />
      </div>

      <div className="flex items-center justify-between">
        <span className="text-[9px] text-muted">{dim.indices.join(" + ")}</span>
        <span className="tabular-nums text-[11px] font-semibold text-fg">{dim.value}</span>
      </div>
    </div>
  );
}

/* ─── Overall gauge ───────────────────────────────────────────────────────── */

function OverallGauge({ score }: { score: number }) {
  const tone = scoreToTone(score);
  const label = scoreToLabel(score);

  const STROKE = 6;
  const R = 30;
  const C = 2 * Math.PI * R;
  const dashOffset = C - (score / 100) * C;

  return (
    <div className="flex items-center gap-4 rounded-lg border border-border bg-panel p-4">
      {/* Ring gauge */}
      <div className="relative size-[72px] shrink-0">
        <svg viewBox="0 0 72 72" className="size-full -rotate-90">
          <circle
            cx={36}
            cy={36}
            r={R}
            fill="none"
            stroke="var(--panel-2)"
            strokeWidth={STROKE}
          />
          <circle
            cx={36}
            cy={36}
            r={R}
            fill="none"
            stroke={
              tone === "positive"
                ? "var(--positive)"
                : tone === "caution"
                  ? "var(--caution)"
                  : tone === "critical"
                    ? "var(--critical)"
                    : "var(--muted)"
            }
            strokeWidth={STROKE}
            strokeLinecap="round"
            strokeDasharray={C}
            strokeDashoffset={dashOffset}
            style={{ transition: "stroke-dashoffset 0.6s ease-out" }}
          />
        </svg>
        <div className="absolute inset-0 flex flex-col items-center justify-center">
          <span className={`text-base font-bold tabular-nums leading-none ${TONE_TEXT[tone]}`}>
            {score}
          </span>
          <span className="text-[9px] text-muted">/ 100</span>
        </div>
      </div>

      <div className="flex flex-col gap-0.5">
        <p className="text-xs font-semibold text-fg">Overall Field Health</p>
        <p className={`text-sm font-bold ${TONE_TEXT[tone]}`}>{label}</p>
        <p className="text-[10px] leading-relaxed text-muted">
          Composite of canopy vigour, nitrogen status, moisture, and ground coverage.
        </p>
      </div>
    </div>
  );
}

/* ─── Finding row ─────────────────────────────────────────────────────────── */

const FINDING_INDICATOR: Record<CorrelationFinding["tone"], string> = {
  positive: "bg-positive",
  caution: "bg-caution",
  critical: "bg-critical",
  neutral: "bg-muted",
};

/* ─── Main export ─────────────────────────────────────────────────────────── */

export interface FieldOverviewProps {
  jobs: Record<IndexKey, AOIJob | undefined>;
}

export function FieldOverview({ jobs }: FieldOverviewProps) {
  const avgs: Partial<Record<IndexKey, number>> = {};
  for (const [key, job] of Object.entries(jobs) as [IndexKey, AOIJob | undefined][]) {
    if (job?.state !== "done") continue;
    const usable = (job.result?.passes ?? []).filter(
      (p) => (p.status === "ok" || p.status === "interpolated") && p.mean != null,
    );
    if (usable.length === 0) continue;
    avgs[key] = usable.reduce((s, p) => s + (p.mean as number), 0) / usable.length;
  }

  const dims = buildDimensions(avgs);
  const overallScore = computeOverall(dims);
  const findings = buildCorrelations(avgs);

  const availableCount = Object.keys(avgs).length;

  if (availableCount === 0) {
    return (
      <div className="flex h-full items-center justify-center p-8 text-center text-muted">
        <p className="text-sm">
          Run all indices to see a cross-index field overview.
        </p>
      </div>
    );
  }

  return (
    <div className="flex flex-col gap-3 p-3">
      {/* Overall gauge */}
      <OverallGauge score={overallScore} />

      {/* Dimension cards */}
      {dims.length > 0 && (
        <div className="grid grid-cols-2 gap-2 sm:grid-cols-4">
          {dims.map((d) => (
            <DimensionCard key={d.key} dim={d} />
          ))}
        </div>
      )}

      {/* Cross-index correlation findings */}
      {findings.length > 0 && (
        <div className="rounded-lg border border-border bg-panel p-3">
          <p className="mb-2 text-[10px] font-medium uppercase tracking-wider text-muted">
            Key Findings
            <span className="ml-1.5 font-normal normal-case text-muted/70">
              — cross-index correlation
            </span>
          </p>
          <ul className="flex flex-col gap-2">
            {findings.map((f, i) => (
              <li key={i} className="flex gap-2.5 text-xs leading-relaxed">
                <span
                  className={`mt-1.5 size-1.5 shrink-0 rounded-full ${FINDING_INDICATOR[f.tone]}`}
                />
                <span className="text-fg/90">{f.text}</span>
              </li>
            ))}
          </ul>
        </div>
      )}

      {/* CDSE calibration reference */}
      <CDSECalibrationPanel />
    </div>
  );
}
