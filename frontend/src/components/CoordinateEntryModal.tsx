import { X } from "@phosphor-icons/react";
import type { Geometry } from "geojson";
import { useState } from "react";

import { cn } from "@/lib/format";

import { Button, SegmentedControl } from "./ui";

interface CoordinateEntryModalProps {
  onClose: () => void;
  onApply: (geometry: Geometry) => void;
  /** Navigate to the area between two points without drawing a boundary. */
  onFitBounds?: (sw: [number, number], ne: [number, number]) => void;
}

type Tab = "bbox" | "polygon" | "twopoints";

interface BBoxValues {
  north: string;
  south: string;
  west: string;
  east: string;
}

interface TwoPointValues {
  lat1: string;
  lon1: string;
  lat2: string;
  lon2: string;
}

function validateBBox(v: BBoxValues): string | null {
  const n = parseFloat(v.north);
  const s = parseFloat(v.south);
  const w = parseFloat(v.west);
  const e = parseFloat(v.east);
  if ([n, s, w, e].some(isNaN)) return "All four values are required.";
  if (n < -90 || n > 90 || s < -90 || s > 90) return "Latitude must be between -90 and 90.";
  if (w < -180 || w > 180 || e < -180 || e > 180)
    return "Longitude must be between -180 and 180.";
  if (n <= s) return "North must be greater than South.";
  if (e <= w) return "East must be greater than West.";
  return null;
}

function validateTwoPoints(v: TwoPointValues): string | null {
  const lat1 = parseFloat(v.lat1);
  const lon1 = parseFloat(v.lon1);
  const lat2 = parseFloat(v.lat2);
  const lon2 = parseFloat(v.lon2);
  if ([lat1, lon1, lat2, lon2].some(isNaN)) return "All four values are required.";
  if (lat1 < -90 || lat1 > 90 || lat2 < -90 || lat2 > 90)
    return "Latitude must be between -90 and 90.";
  if (lon1 < -180 || lon1 > 180 || lon2 < -180 || lon2 > 180)
    return "Longitude must be between -180 and 180.";
  return null;
}

function parsePolygonText(text: string): { ok: true; coords: [number, number][] } | { ok: false; error: string } {
  const trimmed = text.trim();
  if (!trimmed) return { ok: false, error: "Enter coordinates above." };

  // Try JSON parse first — accepts [[lon,lat],...] array.
  try {
    const parsed: unknown = JSON.parse(trimmed);
    if (
      Array.isArray(parsed) &&
      parsed.length >= 3 &&
      parsed.every(
        (p) =>
          Array.isArray(p) &&
          p.length >= 2 &&
          typeof p[0] === "number" &&
          typeof p[1] === "number",
      )
    ) {
      return { ok: true, coords: parsed as [number, number][] };
    }
    return { ok: false, error: "JSON must be an array of [lon, lat] pairs with at least 3 points." };
  } catch {
    // Fall through to line-by-line parsing.
  }

  const lines = trimmed.split("\n").map((l) => l.trim()).filter(Boolean);
  const coords: [number, number][] = [];
  for (const line of lines) {
    const parts = line.split(",");
    if (parts.length < 2) {
      return { ok: false, error: `Cannot parse line: "${line}". Expected "lon,lat".` };
    }
    const lon = parseFloat(parts[0]);
    const lat = parseFloat(parts[1]);
    if (isNaN(lon) || isNaN(lat)) {
      return { ok: false, error: `Invalid numbers on line: "${line}".` };
    }
    coords.push([lon, lat]);
  }
  if (coords.length < 3) {
    return { ok: false, error: "At least 3 coordinate pairs are required." };
  }
  return { ok: true, coords };
}

export function CoordinateEntryModal({ onClose, onApply, onFitBounds }: CoordinateEntryModalProps) {
  const [tab, setTab] = useState<Tab>("bbox");

  // Bounding box state
  const [bbox, setBBox] = useState<BBoxValues>({ north: "", south: "", west: "", east: "" });
  const bboxError = validateBBox(bbox);
  const bboxValid =
    bbox.north !== "" &&
    bbox.south !== "" &&
    bbox.west !== "" &&
    bbox.east !== "" &&
    bboxError === null;

  // Polygon vertices state
  const [polyText, setPolyText] = useState("");
  const polyResult = parsePolygonText(polyText);

  // Two points state
  const [twoPoints, setTwoPoints] = useState<TwoPointValues>({ lat1: "", lon1: "", lat2: "", lon2: "" });
  const twoPointsError = validateTwoPoints(twoPoints);
  const twoPointsFilled =
    twoPoints.lat1 !== "" && twoPoints.lon1 !== "" &&
    twoPoints.lat2 !== "" && twoPoints.lon2 !== "";
  const twoPointsValid = twoPointsFilled && twoPointsError === null;

  function handleBBoxApply() {
    const n = parseFloat(bbox.north);
    const s = parseFloat(bbox.south);
    const w = parseFloat(bbox.west);
    const e = parseFloat(bbox.east);
    const ring: [number, number][] = [
      [w, n],
      [e, n],
      [e, s],
      [w, s],
      [w, n],
    ];
    onApply({ type: "Polygon", coordinates: [ring] });
    onClose();
  }

  function handlePolyApply() {
    if (!polyResult.ok) return;
    const verts = polyResult.coords;
    // Auto-close the ring if the first and last points differ.
    const first = verts[0];
    const last = verts[verts.length - 1];
    const closed =
      first[0] === last[0] && first[1] === last[1] ? verts : [...verts, first];
    onApply({ type: "Polygon", coordinates: [closed] });
    onClose();
  }

  function handleTwoPointsApply() {
    const lat1 = parseFloat(twoPoints.lat1);
    const lon1 = parseFloat(twoPoints.lon1);
    const lat2 = parseFloat(twoPoints.lat2);
    const lon2 = parseFloat(twoPoints.lon2);
    const sw: [number, number] = [Math.min(lon1, lon2), Math.min(lat1, lat2)];
    const ne: [number, number] = [Math.max(lon1, lon2), Math.max(lat1, lat2)];
    onFitBounds?.(sw, ne);
    onClose();
  }

  const numInputClass =
    "w-full rounded-md border border-border bg-panel-2 px-2.5 py-1.5 text-sm text-fg placeholder:text-muted focus:outline-none focus:ring-2 focus:ring-accent";

  const applyDisabled =
    tab === "bbox" ? !bboxValid :
    tab === "polygon" ? !polyResult.ok :
    !twoPointsValid;

  const applyLabel = tab === "twopoints" ? "Navigate to area" : "Apply AOI";

  function handleApply() {
    if (tab === "bbox") handleBBoxApply();
    else if (tab === "polygon") handlePolyApply();
    else handleTwoPointsApply();
  }

  return (
    <div className="absolute inset-0 z-40 flex items-center justify-center bg-bg/60 backdrop-blur-sm">
      <div className="relative w-full max-w-md rounded-xl border border-border bg-panel shadow-xl">
        {/* Header */}
        <div className="flex items-center justify-between border-b border-border px-4 py-3">
          <h2 className="text-sm font-semibold text-fg">Enter coordinates</h2>
          <button
            onClick={onClose}
            aria-label="Close"
            className="rounded-md p-1 text-muted transition-colors hover:bg-panel-2 hover:text-fg"
          >
            <X size={16} />
          </button>
        </div>

        {/* Tab selector */}
        <div className="border-b border-border px-4 py-2.5">
          <SegmentedControl
            ariaLabel="Coordinate input mode"
            options={[
              { value: "bbox" as Tab, label: "Bounding box" },
              { value: "polygon" as Tab, label: "Polygon" },
              { value: "twopoints" as Tab, label: "Two points" },
            ]}
            value={tab}
            onChange={setTab}
          />
        </div>

        {/* Body */}
        <div className="p-4">
          {tab === "bbox" ? (
            <div className="space-y-3">
              <p className="text-xs text-muted">
                Enter decimal degrees. North and South are latitude; West and East are longitude.
              </p>
              {/* North */}
              <div>
                <label className="mb-1 block text-xs text-muted">North (max lat)</label>
                <input
                  type="number"
                  placeholder="e.g. -17.5"
                  value={bbox.north}
                  onChange={(e) => setBBox((b) => ({ ...b, north: e.target.value }))}
                  className={numInputClass}
                />
              </div>
              {/* South/West/East in a grid */}
              <div className="grid grid-cols-2 gap-2">
                <div>
                  <label className="mb-1 block text-xs text-muted">West (min lon)</label>
                  <input
                    type="number"
                    placeholder="e.g. 29.8"
                    value={bbox.west}
                    onChange={(e) => setBBox((b) => ({ ...b, west: e.target.value }))}
                    className={numInputClass}
                  />
                </div>
                <div>
                  <label className="mb-1 block text-xs text-muted">East (max lon)</label>
                  <input
                    type="number"
                    placeholder="e.g. 30.2"
                    value={bbox.east}
                    onChange={(e) => setBBox((b) => ({ ...b, east: e.target.value }))}
                    className={numInputClass}
                  />
                </div>
              </div>
              <div>
                <label className="mb-1 block text-xs text-muted">South (min lat)</label>
                <input
                  type="number"
                  placeholder="e.g. -18.0"
                  value={bbox.south}
                  onChange={(e) => setBBox((b) => ({ ...b, south: e.target.value }))}
                  className={numInputClass}
                />
              </div>
              {/* Validation feedback */}
              {bbox.north !== "" && bbox.south !== "" && bbox.west !== "" && bbox.east !== "" && bboxError && (
                <p className="text-xs text-critical">{bboxError}</p>
              )}
              {bboxValid && (
                <p className="text-xs text-positive">Valid bounding box</p>
              )}
            </div>
          ) : tab === "polygon" ? (
            <div className="space-y-3">
              <p className="text-xs text-muted">
                One <code className="rounded bg-panel-2 px-1 text-fg">lon,lat</code> pair per line,
                or paste a JSON array of <code className="rounded bg-panel-2 px-1 text-fg">[[lon,lat],...]</code>.
                Minimum 3 points.
              </p>
              <textarea
                rows={8}
                placeholder={"30.1,-17.9\n30.2,-17.9\n30.2,-18.0\n30.1,-18.0"}
                value={polyText}
                onChange={(e) => setPolyText(e.target.value)}
                className="w-full resize-y rounded-md border border-border bg-panel-2 px-2.5 py-2 font-mono text-xs text-fg placeholder:text-muted focus:outline-none focus:ring-2 focus:ring-accent"
              />
              {polyText.trim() !== "" && !polyResult.ok && (
                <p className="text-xs text-critical">{polyResult.error}</p>
              )}
              {polyText.trim() !== "" && polyResult.ok && (
                <p className="text-xs text-positive">
                  {polyResult.coords.length} vertices parsed
                </p>
              )}
            </div>
          ) : (
            <div className="space-y-3">
              <p className="text-xs text-muted">
                Enter two lat/lon pairs in decimal degrees. The map navigates to the area
                between them. No boundary is drawn.
              </p>
              {/* Point A */}
              <div>
                <span className="mb-1.5 block text-xs font-medium text-muted">Point A</span>
                <div className="grid grid-cols-2 gap-2">
                  <div>
                    <label className="mb-1 block text-xs text-muted">Latitude</label>
                    <input
                      type="number"
                      placeholder="e.g. -17.9"
                      value={twoPoints.lat1}
                      onChange={(e) => setTwoPoints((p) => ({ ...p, lat1: e.target.value }))}
                      className={numInputClass}
                    />
                  </div>
                  <div>
                    <label className="mb-1 block text-xs text-muted">Longitude</label>
                    <input
                      type="number"
                      placeholder="e.g. 30.1"
                      value={twoPoints.lon1}
                      onChange={(e) => setTwoPoints((p) => ({ ...p, lon1: e.target.value }))}
                      className={numInputClass}
                    />
                  </div>
                </div>
              </div>
              {/* Point B */}
              <div>
                <span className="mb-1.5 block text-xs font-medium text-muted">Point B</span>
                <div className="grid grid-cols-2 gap-2">
                  <div>
                    <label className="mb-1 block text-xs text-muted">Latitude</label>
                    <input
                      type="number"
                      placeholder="e.g. -18.2"
                      value={twoPoints.lat2}
                      onChange={(e) => setTwoPoints((p) => ({ ...p, lat2: e.target.value }))}
                      className={numInputClass}
                    />
                  </div>
                  <div>
                    <label className="mb-1 block text-xs text-muted">Longitude</label>
                    <input
                      type="number"
                      placeholder="e.g. 30.4"
                      value={twoPoints.lon2}
                      onChange={(e) => setTwoPoints((p) => ({ ...p, lon2: e.target.value }))}
                      className={numInputClass}
                    />
                  </div>
                </div>
              </div>
              {/* Validation feedback */}
              {twoPointsFilled && twoPointsError && (
                <p className="text-xs text-critical">{twoPointsError}</p>
              )}
              {twoPointsValid && (
                <p className="text-xs text-positive">Ready to navigate</p>
              )}
            </div>
          )}
        </div>

        {/* Footer */}
        <div className={cn("flex justify-end gap-2 border-t border-border px-4 py-3")}>
          <Button variant="ghost" onClick={onClose}>
            Cancel
          </Button>
          <Button
            variant="primary"
            onClick={handleApply}
            disabled={applyDisabled}
          >
            {applyLabel}
          </Button>
        </div>
      </div>
    </div>
  );
}
