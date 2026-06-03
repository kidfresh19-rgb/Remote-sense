export type IndexKey = "ndvi" | "evi2" | "savi" | "ndre" | "ndmi";

export interface IndexMeta {
  key: IndexKey;
  label: string;
  long: string;
  description: string;
  min: number;
  max: number;
  gradient: string[];
}

// Vegetation ramp (red low to green high) and a moisture ramp (brown dry to teal wet).
const VEG = [
  "#a50026",
  "#d73027",
  "#f46d43",
  "#fdae61",
  "#fee08b",
  "#d9ef8b",
  "#a6d96a",
  "#66bd63",
  "#1a9850",
  "#006837",
];
const MOIST = [
  "#8c510a",
  "#bf812d",
  "#dfc27d",
  "#f6e8c3",
  "#f5f5f5",
  "#c7eae5",
  "#80cdc1",
  "#35978f",
  "#01665e",
];

// Display ranges + ramps are kept in lockstep with rs_analysis/colormaps.py so the legend matches
// the rendered tiles. ⚑ CONFIRM: v1 proposed 2026-06-03, pending agronomist sign-off.
export const INDICES: IndexMeta[] = [
  {
    key: "ndvi",
    label: "NDVI",
    long: "Normalised Difference Vegetation Index",
    description: "Canopy greenness and vigour.",
    min: -0.2,
    max: 0.9,
    gradient: VEG,
  },
  {
    key: "evi2",
    label: "EVI2",
    long: "Two-band Enhanced Vegetation Index",
    description: "Vigour with reduced saturation over dense canopy.",
    min: -0.1,
    max: 0.8,
    gradient: VEG,
  },
  {
    key: "savi",
    label: "SAVI",
    long: "Soil-Adjusted Vegetation Index",
    description: "Vigour corrected for bare-soil background.",
    min: -0.1,
    max: 0.7,
    gradient: VEG,
  },
  {
    key: "ndre",
    label: "NDRE",
    long: "Normalised Difference Red-Edge",
    description: "Chlorophyll and nitrogen status (20 m).",
    min: -0.1,
    max: 0.6,
    gradient: VEG,
  },
  {
    key: "ndmi",
    label: "NDMI",
    long: "Normalised Difference Moisture Index",
    description: "Canopy water content (20 m).",
    min: -0.3,
    max: 0.5,
    gradient: MOIST,
  },
];

export const DEFAULT_INDEX: IndexKey = "ndvi";

const BY_KEY = new Map(INDICES.map((m) => [m.key, m]));

export function indexMeta(key: IndexKey): IndexMeta {
  const meta = BY_KEY.get(key);
  if (!meta) throw new Error(`unknown index ${key}`);
  return meta;
}

function hexToRgb(hex: string): [number, number, number] {
  const h = hex.replace("#", "");
  return [parseInt(h.slice(0, 2), 16), parseInt(h.slice(2, 4), 16), parseInt(h.slice(4, 6), 16)];
}

function lerp(a: number, b: number, t: number): number {
  return Math.round(a + (b - a) * t);
}

/** Color a measured value by its position in the index ramp. Used for chart points and markers. */
export function colorForValue(meta: IndexMeta, value: number): string {
  const span = meta.max - meta.min || 1;
  const t = Math.min(1, Math.max(0, (value - meta.min) / span));
  const stops = meta.gradient;
  const scaled = t * (stops.length - 1);
  const i = Math.min(stops.length - 2, Math.floor(scaled));
  const f = scaled - i;
  const [r1, g1, b1] = hexToRgb(stops[i]);
  const [r2, g2, b2] = hexToRgb(stops[i + 1]);
  return `rgb(${lerp(r1, r2, f)}, ${lerp(g1, g2, f)}, ${lerp(b1, b2, f)})`;
}

export function gradientCss(meta: IndexMeta): string {
  return `linear-gradient(to right, ${meta.gradient.join(", ")})`;
}
