import maplibregl, { type GeoJSONSource, type StyleSpecification } from "maplibre-gl";
import { useEffect, useRef, useState } from "react";
import type { FeatureCollection } from "geojson";

import { bboxOf } from "@/components/useFieldMap";
import { config } from "@/lib/config";
import { useFarms, useAllFields } from "@/lib/queries";
import type { Farm, Field } from "@/lib/api";

function cssVar(name: string, fallback: string): string {
  if (typeof document === "undefined") return fallback;
  return getComputedStyle(document.documentElement).getPropertyValue(name).trim() || fallback;
}

function overviewStyle(): string | StyleSpecification {
  if (config.basemapUrl === "none") {
    return {
      version: 8,
      sources: {},
      layers: [{ id: "bg", type: "background", paint: { "background-color": cssVar("--panel-2", "#1b1b21") } }],
    };
  }
  if (config.basemapUrl) return config.basemapUrl;
  return {
    version: 8,
    sources: {
      satellite: {
        type: "raster",
        tiles: [
          "https://server.arcgisonline.com/ArcGIS/rest/services/World_Imagery/MapServer/tile/{z}/{y}/{x}",
        ],
        tileSize: 256,
        maxzoom: 19,
        attribution: "Imagery © Esri, Maxar, Earthstar Geographics",
      },
      labels: {
        type: "raster",
        tiles: ["a", "b", "c", "d"].map(
          (s) => `https://${s}.basemaps.cartocdn.com/dark_only_labels/{z}/{x}/{y}.png`,
        ),
        tileSize: 256,
        maxzoom: 20,
        attribution: "© OpenStreetMap contributors © CARTO",
      },
    },
    layers: [
      { id: "bg", type: "background", paint: { "background-color": cssVar("--bg", "#0b0b0d") } },
      { id: "satellite", type: "raster", source: "satellite" },
      { id: "labels", type: "raster", source: "labels", paint: { "raster-opacity": 0.9 } },
    ],
  };
}

function buildGeoJSON(farms: Farm[], allFields: (Field[] | undefined)[]): FeatureCollection {
  const features: FeatureCollection["features"] = [];
  farms.forEach((farm, i) => {
    for (const field of allFields[i] ?? []) {
      features.push({
        type: "Feature",
        geometry: field.geometry,
        properties: {
          fieldId: field.field_id,
          farmId: farm.canonical_farm_id,
          health: farm.overall_health ?? "unknown",
        },
      });
    }
  });
  return { type: "FeatureCollection", features };
}

function combinedBbox(
  farms: Farm[],
  allFields: (Field[] | undefined)[],
): [number, number, number, number] | null {
  let minX = Infinity,
    minY = Infinity,
    maxX = -Infinity,
    maxY = -Infinity;
  farms.forEach((_, i) => {
    for (const field of allFields[i] ?? []) {
      const bb = bboxOf(field.geometry);
      if (bb) {
        minX = Math.min(minX, bb[0]);
        minY = Math.min(minY, bb[1]);
        maxX = Math.max(maxX, bb[2]);
        maxY = Math.max(maxY, bb[3]);
      }
    }
  });
  return Number.isFinite(minX) ? [minX, minY, maxX, maxY] : null;
}

const HEALTH_PAINT = [
  "match",
  ["get", "health"],
  "healthy",
  "#3d8b3d",
  "moderate",
  "#b07908",
  "stressed",
  "#c87928",
  "critical",
  "#c0463b",
  "#8b8b95",
] as maplibregl.ExpressionSpecification;

const LEGEND = [
  { label: "Healthy", color: "#3d8b3d" },
  { label: "Moderate", color: "#b07908" },
  { label: "Stressed", color: "#c87928" },
  { label: "Critical", color: "#c0463b" },
];

export function FieldHealthMap() {
  const containerRef = useRef<HTMLDivElement>(null);
  const mapRef = useRef<maplibregl.Map | null>(null);
  const hasFitRef = useRef(false);
  const [mapLoaded, setMapLoaded] = useState(false);

  const farms = useFarms();
  const fieldResults = useAllFields(farms.data);

  // Stable key derived from actual field IDs so the data effect only fires when content changes.
  const fieldIdKey = fieldResults
    .map((r) => r.data?.map((f) => f.field_id).join("-") ?? "pending")
    .join("|");

  // Initialise map once.
  useEffect(() => {
    if (!containerRef.current || mapRef.current) return;
    const map = new maplibregl.Map({
      container: containerRef.current,
      style: overviewStyle(),
      center: [29.15, -19.02],
      zoom: 5.5,
      attributionControl: false,
      interactive: false,
    });
    map.on("load", () => {
      map.addSource("fields", {
        type: "geojson",
        data: { type: "FeatureCollection", features: [] },
      });
      map.addLayer({
        id: "fields-fill",
        type: "fill",
        source: "fields",
        paint: { "fill-color": HEALTH_PAINT, "fill-opacity": 0.65 },
      });
      map.addLayer({
        id: "fields-line",
        type: "line",
        source: "fields",
        paint: { "line-color": HEALTH_PAINT, "line-width": 1.5, "line-opacity": 0.9 },
      });
      setMapLoaded(true);
    });
    mapRef.current = map;
    return () => {
      map.remove();
      mapRef.current = null;
      hasFitRef.current = false;
      setMapLoaded(false);
    };
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, []);

  // Update GeoJSON source when farm/field data changes.
  useEffect(() => {
    const map = mapRef.current;
    if (!map || !mapLoaded || !farms.data) return;

    const allFields = fieldResults.map((r) => r.data);
    const geojson = buildGeoJSON(farms.data, allFields);
    const src = map.getSource("fields") as GeoJSONSource | undefined;
    if (!src) return;
    src.setData(geojson);

    if (!hasFitRef.current) {
      const bb = combinedBbox(farms.data, allFields);
      if (bb) {
        map.fitBounds(bb, { padding: 48, maxZoom: 14, animate: false });
        hasFitRef.current = true;
      }
    }
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [mapLoaded, farms.data, fieldIdKey]);

  return (
    <div className="relative h-full w-full">
      <div ref={containerRef} className="absolute inset-0" />
      <div className="absolute bottom-3 left-3 flex flex-col gap-1.5 rounded-lg border border-border bg-panel/90 px-3 py-2.5 backdrop-blur-sm">
        {LEGEND.map(({ label, color }) => (
          <div key={label} className="flex items-center gap-2">
            <span className="size-2.5 rounded-full" style={{ background: color }} />
            <span className="text-xs text-muted">{label}</span>
          </div>
        ))}
      </div>
    </div>
  );
}
