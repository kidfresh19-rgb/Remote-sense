import { useState } from "react";
import { CaretDown, CaretRight, CheckCircle, TrendDown, TrendUp, Minus } from "@phosphor-icons/react";

import { dateValue, formatDateShort, formatNumber, formatPercent } from "@/lib/format";
import { colorForValue, gradientCss, indexMeta, type IndexKey } from "@/lib/indices";
import {
  averages,
  buildCorrelations,
  buildDimensions,
  computeOverall,
  indexLineColor,
  INDEX_KEYS,
  latestSpread,
  scoreToLabel,
  scoreToTone,
  seriesAverage,
  seriesLatest,
  seriesTrend,
  summarise,
  type CorrelationFinding,
  type HealthDimension,
  type IndexSeries,
  type OverviewSeries,
  type Tone,
  type Trend,
} from "@/lib/fieldHealth";

/* ─── Tone maps ───────────────────────────────────────────────────────────── */

const TONE_BAR: Record<Tone, string> = {
  positive: "bg-positive",
  caution: "bg-caution",
  critical: "bg-critical",
  neutral: "bg-muted",
};

const TONE_TEXT: Record<Tone, string> = {
  positive: "text-positive",
  caution: "text-caution",
  critical: "text-critical",
  neutral: "text-muted",
};

const TONE_VAR: Record<Tone, string> = {
  positive: "var(--positive)",
  caution: "var(--caution)",
  critical: "var(--critical)",
  neutral: "var(--muted)",
};

/** Position of a value on its index's display range, 0-1 (clamped). Drives the swatch marker. */
function rangePos(key: IndexKey, value: number): number {
  const meta = indexMeta(key);
  return Math.min(1, Math.max(0, (value - meta.min) / (meta.max - meta.min || 1)));
}

/* ─── CDSE Validation Matrix (pinned against Copernicus Browser Process API, 2026-06-04) ── */

type CDSERow = { ndvi: number; evi2: number; savi: number; ndre: number; ndmi: number };
const CDSE_INDICES: (keyof CDSERow)[] = ["ndvi", "evi2", "savi", "ndre", "ndmi"];

const CDSE_MATRIX: { label: string; scene: string; rs: CDSERow; browser: CDSERow }[] = [
  {
    label: "Harare cropland",
    scene: "S2A · 2026-05-17 · T36KTF",
    rs: { ndvi: 0.38, evi2: 0.193, savi: 0.204, ndre: 0.2418, ndmi: -0.0179 },
    browser: { ndvi: 0.3856, evi2: 0.1938, savi: 0.2049, ndre: 0.246, ndmi: -0.0152 },
  },
  {
    label: "Mazowe Valley",
    scene: "S2A · 2026-05-17 · T36KTF",
    rs: { ndvi: 0.3804, evi2: 0.1831, savi: 0.1967, ndre: 0.219, ndmi: -0.1469 },
    browser: { ndvi: 0.3815, evi2: 0.1832, savi: 0.1969, ndre: 0.2199, ndmi: -0.1463 },
  },
  {
    label: "Harare cropland",
    scene: "S2C · 2026-05-05 · T36KTF",
    rs: { ndvi: 0.4076, evi2: 0.2072, savi: 0.2182, ndre: 0.2481, ndmi: -0.0082 },
    browser: { ndvi: 0.4083, evi2: 0.2079, savi: 0.2189, ndre: 0.2488, ndmi: -0.0076 },
  },
];

/* ─── Overall gauge (enlarged) ────────────────────────────────────────────── */

function OverallGauge({ score }: { score: number }) {
  const tone = scoreToTone(score);
  const label = scoreToLabel(score);

  const STROKE = 8;
  const R = 42;
  const C = 2 * Math.PI * R;
  const dashOffset = C - (score / 100) * C;

  return (
    <div className="flex items-center gap-5 rounded-xl border border-border bg-panel p-5">
      <div className="relative size-[104px] shrink-0">
        <svg viewBox="0 0 104 104" className="size-full -rotate-90">
          <circle cx={52} cy={52} r={R} fill="none" stroke="var(--panel-2)" strokeWidth={STROKE} />
          <circle
            cx={52}
            cy={52}
            r={R}
            fill="none"
            stroke={TONE_VAR[tone]}
            strokeWidth={STROKE}
            strokeLinecap="round"
            strokeDasharray={C}
            strokeDashoffset={dashOffset}
            style={{ transition: "stroke-dashoffset 0.6s ease-out" }}
          />
        </svg>
        <div className="absolute inset-0 flex flex-col items-center justify-center">
          <span className={`text-3xl font-bold tabular-nums leading-none ${TONE_TEXT[tone]}`}>
            {score}
          </span>
          <span className="text-[10px] text-muted">/ 100</span>
        </div>
      </div>

      <div className="flex min-w-0 flex-col gap-1">
        <p className="text-sm font-semibold text-fg">Overall Field Health</p>
        <p className={`text-xl font-bold leading-tight ${TONE_TEXT[tone]}`}>{label}</p>
        <p className="text-[11px] leading-relaxed text-muted">
          Weighted composite of canopy vigour, nitrogen status, moisture, and ground coverage.
        </p>
      </div>
    </div>
  );
}

/* ─── Summary tiles ───────────────────────────────────────────────────────── */

function SummaryTiles({ series }: { series: OverviewSeries }) {
  const s = summarise(series);
  const window =
    s.dateStart && s.dateEnd
      ? s.dateStart === s.dateEnd
        ? formatDateShort(s.dateStart)
        : `${formatDateShort(s.dateStart)} – ${formatDateShort(s.dateEnd)}`
      : "·";

  const tiles: { label: string; value: string }[] = [
    { label: "Indices", value: `${s.indexCount} / ${INDEX_KEYS.length}` },
    { label: "Clear passes", value: String(s.passCount) },
    { label: "Window", value: window },
    { label: "Avg clear px", value: formatPercent(s.meanClearFraction) },
  ];

  return (
    <div className="grid grid-cols-2 gap-2 sm:grid-cols-4">
      {tiles.map((t) => (
        <div key={t.label} className="rounded-lg border border-border bg-panel px-3 py-2.5">
          <p className="text-[9px] font-bold uppercase tracking-wider text-muted">{t.label}</p>
          <p className="mt-1 truncate text-sm font-semibold tabular-nums text-fg">{t.value}</p>
        </div>
      ))}
    </div>
  );
}

/* ─── Trend chip ──────────────────────────────────────────────────────────── */

function TrendChip({ trend }: { trend: Trend }) {
  if (trend.dir === "flat") {
    return (
      <span className="flex items-center gap-0.5 text-[10px] font-medium text-muted">
        <Minus size={11} weight="bold" /> steady
      </span>
    );
  }
  const up = trend.dir === "up";
  return (
    <span
      className={`flex items-center gap-0.5 text-[10px] font-medium tabular-nums ${up ? "text-positive" : "text-critical"}`}
    >
      {up ? <TrendUp size={11} weight="bold" /> : <TrendDown size={11} weight="bold" />}
      {up ? "+" : ""}
      {formatNumber(trend.delta, 2)}
    </span>
  );
}

/* ─── Index swatch legend ─────────────────────────────────────────────────── */

function IndexSwatchRow({ s }: { s: IndexSeries }) {
  const meta = indexMeta(s.key);
  const avg = seriesAverage(s);
  const latest = seriesLatest(s);
  const trend = seriesTrend(s);
  const markerPct = rangePos(s.key, avg) * 100;

  return (
    <div className="flex items-center gap-3">
      <span className="w-11 shrink-0 text-[11px] font-semibold text-fg">{meta.label}</span>

      {/* Gradient ramp with a marker at the window average */}
      <div className="relative h-2.5 flex-1 overflow-hidden rounded-full" style={{ background: gradientCss(meta) }}>
        <span
          className="absolute top-1/2 size-3 -translate-x-1/2 -translate-y-1/2 rounded-full border-2 border-panel shadow"
          style={{ left: `${markerPct}%`, background: colorForValue(meta, avg) }}
          title={`avg ${formatNumber(avg)}`}
        />
      </div>

      <span
        className="w-12 shrink-0 text-right text-xs font-semibold tabular-nums"
        style={{ color: colorForValue(meta, avg) }}
      >
        {formatNumber(avg)}
      </span>
      <span className="hidden w-16 shrink-0 justify-end sm:flex">
        <TrendChip trend={trend} />
      </span>
      <span className="w-14 shrink-0 text-right text-[10px] text-muted">
        now {formatNumber(latest.mean, 2)}
      </span>
    </div>
  );
}

function IndexSwatchLegend({ series }: { series: OverviewSeries }) {
  const rows = INDEX_KEYS.map((k) => series[k]).filter((s): s is IndexSeries => !!s);
  if (rows.length === 0) return null;
  return (
    <div className="rounded-lg border border-border bg-panel p-3.5">
      <p className="mb-2.5 text-[10px] font-medium uppercase tracking-wider text-muted">
        Index readings
        <span className="ml-1.5 font-normal normal-case text-muted/70">— window average on ramp</span>
      </p>
      <div className="flex flex-col gap-2.5">
        {rows.map((s) => (
          <IndexSwatchRow key={s.key} s={s} />
        ))}
      </div>
    </div>
  );
}

/* ─── Multi-index trend chart (inline SVG, matches house style) ───────────── */

const CHART_W = 520;
const CHART_H = 150;
const CHART_PAD = { l: 8, r: 8, t: 12, b: 20 };

function MultiIndexTrendChart({ series }: { series: OverviewSeries }) {
  const rows = INDEX_KEYS.map((k) => series[k]).filter(
    (s): s is IndexSeries => !!s && s.points.length >= 2,
  );
  if (rows.length === 0) return null;

  const allX = rows.flatMap((s) => s.points.map((p) => dateValue(p.date)));
  const minX = Math.min(...allX);
  const maxX = Math.max(...allX);
  const spanX = maxX - minX || 1;

  const sx = (v: number) => CHART_PAD.l + ((v - minX) / spanX) * (CHART_W - CHART_PAD.l - CHART_PAD.r);
  // Y axis is a 0-100 health scale: each index normalised to its own display range so vigour and
  // moisture share one comparable trajectory.
  const sy = (score: number) =>
    CHART_PAD.t + (1 - score / 100) * (CHART_H - CHART_PAD.t - CHART_PAD.b);

  const lines = rows.map((s) => {
    const color = indexLineColor(s.key, seriesAverage(s));
    const d = s.points
      .map((p, i) => `${i === 0 ? "M" : "L"} ${sx(dateValue(p.date)).toFixed(1)} ${sy(rangePos(s.key, p.mean) * 100).toFixed(1)}`)
      .join(" ");
    return { key: s.key, label: indexMeta(s.key).label, color, d, points: s.points };
  });

  const gridScores = [0, 25, 50, 75, 100];
  const tickDates = [rows[0].points[0].date, rows[0].points[rows[0].points.length - 1].date];

  return (
    <div className="rounded-lg border border-border bg-panel p-3.5">
      <div className="mb-1 flex items-center justify-between">
        <p className="text-[10px] font-medium uppercase tracking-wider text-muted">
          Index trajectories
          <span className="ml-1.5 font-normal normal-case text-muted/70">— normalised health scale</span>
        </p>
        <div className="flex flex-wrap items-center justify-end gap-x-2.5 gap-y-1">
          {lines.map((l) => (
            <span key={l.key} className="flex items-center gap-1 text-[10px] text-muted">
              <span className="inline-block h-0.5 w-3 rounded-full" style={{ background: l.color }} />
              {l.label}
            </span>
          ))}
        </div>
      </div>

      <svg
        viewBox={`0 0 ${CHART_W} ${CHART_H}`}
        className="w-full"
        role="img"
        aria-label="Normalised index trajectories over the analysis window"
      >
        {gridScores.map((g) => (
          <line
            key={g}
            x1={CHART_PAD.l}
            x2={CHART_W - CHART_PAD.r}
            y1={sy(g)}
            y2={sy(g)}
            stroke="var(--border)"
            strokeWidth={1}
            strokeDasharray="3 3"
            opacity={0.4}
          />
        ))}
        {tickDates.map((d, i) => (
          <text
            key={d}
            x={i === 0 ? CHART_PAD.l : CHART_W - CHART_PAD.r}
            y={CHART_H - 6}
            textAnchor={i === 0 ? "start" : "end"}
            fontSize={8}
            className="fill-[var(--muted)] font-mono"
          >
            {formatDateShort(d)}
          </text>
        ))}
        {lines.map((l) => (
          <g key={l.key}>
            <path d={l.d} fill="none" stroke={l.color} strokeWidth={1.75} strokeLinejoin="round" strokeLinecap="round" />
            {l.points.map((p) => (
              <circle
                key={p.date}
                cx={sx(dateValue(p.date))}
                cy={sy(rangePos(l.key, p.mean) * 100)}
                r={2}
                fill={l.color}
                stroke="var(--panel)"
                strokeWidth={0.75}
                opacity={0.5 + 0.5 * p.clearFraction}
              />
            ))}
          </g>
        ))}
      </svg>
    </div>
  );
}

/* ─── Sparkline (inline SVG) ──────────────────────────────────────────────── */

function Sparkline({ s }: { s: IndexSeries }) {
  const meta = indexMeta(s.key);
  const w = 100;
  const h = 26;
  if (s.points.length < 2) {
    return <div className="h-[26px] rounded bg-panel-2/50" title="Single pass — no trend" />;
  }
  const xs = s.points.map((p) => dateValue(p.date));
  const minX = Math.min(...xs);
  const spanX = Math.max(...xs) - minX || 1;
  const px = (v: number) => ((v - minX) / spanX) * w;
  const py = (v: number) => h - rangePos(s.key, v) * h;
  const d = s.points.map((p, i) => `${i === 0 ? "M" : "L"} ${px(xs[i]).toFixed(1)} ${py(p.mean).toFixed(1)}`).join(" ");
  const area = `${d} L ${px(xs[xs.length - 1]).toFixed(1)} ${h} L ${px(xs[0]).toFixed(1)} ${h} Z`;
  const color = colorForValue(meta, seriesLatest(s).mean);
  const gid = `spark-${s.key}`;

  return (
    <svg viewBox={`0 0 ${w} ${h}`} preserveAspectRatio="none" className="h-[26px] w-full" aria-hidden="true">
      <defs>
        <linearGradient id={gid} x1="0" y1="0" x2="0" y2="1">
          <stop offset="0%" stopColor={color} stopOpacity="0.25" />
          <stop offset="100%" stopColor={color} stopOpacity="0.01" />
        </linearGradient>
      </defs>
      <path d={area} fill={`url(#${gid})`} />
      <path d={d} fill="none" stroke={color} strokeWidth={1.5} strokeLinejoin="round" strokeLinecap="round" />
    </svg>
  );
}

/* ─── Dimension card (enriched) ───────────────────────────────────────────── */

function DimensionCard({ dim, series }: { dim: HealthDimension; series: OverviewSeries }) {
  const s = series[dim.primaryKey];
  const trend = s ? seriesTrend(s) : null;
  const spread = s ? latestSpread(s) : null;

  return (
    <div className="flex flex-col gap-2 rounded-lg border border-border bg-panel p-3">
      <div className="flex items-start justify-between gap-1">
        <div className="min-w-0">
          <p className="text-[11px] font-semibold leading-tight text-fg">{dim.label}</p>
          <p className="text-[9px] leading-tight text-muted">{dim.subtitle}</p>
        </div>
        <span className={`shrink-0 text-[10px] font-medium ${TONE_TEXT[dim.tone]}`}>
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

      {/* Sparkline trajectory */}
      {s ? <Sparkline s={s} /> : null}

      <div className="flex items-center justify-between">
        <span className="text-[9px] text-muted">{dim.indices.join(" + ")}</span>
        <span className="text-sm font-semibold tabular-nums" style={{ color: colorForValue(indexMeta(dim.primaryKey), dim.value) }}>
          {formatNumber(dim.value)}
        </span>
      </div>

      <div className="flex items-center justify-between border-t border-border/50 pt-1.5 text-[9px] text-muted">
        {trend ? <TrendChip trend={trend} /> : <span>·</span>}
        {spread != null ? (
          <span title="Latest p10–p90 spread across the AOI (lower = more uniform)">
            spread {formatPercent(spread)}
          </span>
        ) : (
          <span>·</span>
        )}
      </div>
    </div>
  );
}

/* ─── Findings ────────────────────────────────────────────────────────────── */

const FINDING_INDICATOR: Record<Tone, string> = {
  positive: "bg-positive",
  caution: "bg-caution",
  critical: "bg-critical",
  neutral: "bg-muted",
};

function Findings({ findings }: { findings: CorrelationFinding[] }) {
  if (findings.length === 0) return null;
  return (
    <div className="rounded-lg border border-border bg-panel p-3.5">
      <p className="mb-2 text-[10px] font-medium uppercase tracking-wider text-muted">
        Key Findings
        <span className="ml-1.5 font-normal normal-case text-muted/70">— cross-index correlation</span>
      </p>
      <ul className="flex flex-col gap-2.5">
        {findings.map((f, i) => (
          <li key={i} className="flex gap-2.5 text-xs leading-relaxed">
            <span className={`mt-1.5 size-1.5 shrink-0 rounded-full ${FINDING_INDICATOR[f.tone]}`} />
            <span className="text-fg/90">{f.text}</span>
          </li>
        ))}
      </ul>
    </div>
  );
}

/* ─── CDSE Calibration panel (unchanged) ──────────────────────────────────── */

function CDSECalibrationPanel() {
  const [open, setOpen] = useState(false);

  return (
    <div className="overflow-hidden rounded-lg border border-border/50 bg-panel-2/40">
      <button
        onClick={() => setOpen((v) => !v)}
        className="flex w-full items-center gap-2 px-3 py-2 text-left text-[11px] text-muted transition-colors hover:text-fg"
      >
        <span className="flex items-center gap-1.5 text-positive">
          <CheckCircle size={13} weight="fill" />
          <span className="font-medium text-fg">CDSE validated</span>
        </span>
        <span className="flex-1 text-muted">Values calibrated against Copernicus Browser within ±0.01</span>
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
                  <th className="min-w-[110px] py-1.5 pr-3 font-medium">Scene</th>
                  <th className="py-1.5 pr-2 text-right font-medium">NDVI</th>
                  <th className="py-1.5 pr-2 text-right font-medium">EVI2</th>
                  <th className="py-1.5 pr-2 text-right font-medium">SAVI</th>
                  <th className="py-1.5 pr-2 text-right font-medium">NDRE</th>
                  <th className="py-1.5 text-right font-medium">NDMI</th>
                </tr>
              </thead>
              <tbody>
                {CDSE_MATRIX.map((row, i) => {
                  const deltas = CDSE_INDICES.map((k) => row.browser[k] - row.rs[k]);
                  return (
                    <tr key={i} className="border-b border-border/40">
                      <td className="py-1.5 pr-3 align-top">
                        <p className="font-medium text-fg">{row.label}</p>
                        <p className="font-mono text-[9px] leading-tight text-muted">{row.scene}</p>
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

/* ─── Main export ─────────────────────────────────────────────────────────── */

export interface FieldOverviewProps {
  series: OverviewSeries;
  /** Message shown when no index has usable data yet. Differs by surface (Studio vs workspace). */
  emptyHint?: string;
}

export function FieldOverview({ series, emptyHint }: FieldOverviewProps) {
  const avgs = averages(series);
  const dims = buildDimensions(series);
  const overallScore = computeOverall(dims);
  const findings = buildCorrelations(series);
  const availableCount = Object.keys(avgs).length;

  if (availableCount === 0) {
    return (
      <div className="flex h-full items-center justify-center p-8 text-center text-muted">
        <p className="text-sm">{emptyHint ?? "Run all indices to see a cross-index field overview."}</p>
      </div>
    );
  }

  return (
    <div className="flex flex-col gap-4 p-4">
      {/* Header: gauge + summary */}
      <div className="grid gap-3 lg:grid-cols-[minmax(0,1fr)_minmax(0,1.1fr)]">
        <OverallGauge score={overallScore} />
        <SummaryTiles series={series} />
      </div>

      {/* Index swatch legend */}
      <IndexSwatchLegend series={series} />

      {/* Multi-index trend chart */}
      <MultiIndexTrendChart series={series} />

      {/* Dimension cards */}
      {dims.length > 0 && (
        <div className="grid grid-cols-2 gap-2.5 lg:grid-cols-4">
          {dims.map((d) => (
            <DimensionCard key={d.key} dim={d} series={series} />
          ))}
        </div>
      )}

      {/* Key findings */}
      <Findings findings={findings} />

      {/* CDSE calibration reference */}
      <CDSECalibrationPanel />
    </div>
  );
}
