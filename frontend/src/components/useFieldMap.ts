import type { Geometry } from "geojson";
import maplibregl, {
  type GeoJSONSource,
  type Map as MaplibreMap,
  type StyleSpecification,
} from "maplibre-gl";
import { useEffect, useRef, type RefObject } from "react";

import { indexTileTemplate, type Field } from "@/lib/api";
import { config } from "@/lib/config";
import type { IndexKey } from "@/lib/indices";

const FIELD_SOURCE = "field";
const FIELD_FILL = "field-fill";
const FIELD_LINE = "field-line";
const INDEX_SOURCE = "index-raster";
const INDEX_LAYER = "index-raster-layer";

function cssVar(name: string, fallback: string): string {
  const value = getComputedStyle(document.documentElement).getPropertyValue(name).trim();
  return value || fallback;
}

function minimalStyle(): StyleSpecification {
  // Key-free default: a flat background plus whatever layers we add at runtime. Set VITE_BASEMAP_URL
  // to a real vector/raster style for geographic context.
  return {
    version: 8,
    sources: {},
    layers: [
      { id: "bg", type: "background", paint: { "background-color": cssVar("--bg", "#0b0b0d") } },
    ],
  };
}

function bboxOf(geometry: Geometry): [number, number, number, number] | null {
  if (!("coordinates" in geometry)) return null;
  let minX = Infinity;
  let minY = Infinity;
  let maxX = -Infinity;
  let maxY = -Infinity;
  const visit = (node: unknown): void => {
    if (Array.isArray(node) && typeof node[0] === "number") {
      const [x, y] = node as number[];
      if (x < minX) minX = x;
      if (y < minY) minY = y;
      if (x > maxX) maxX = x;
      if (y > maxY) maxY = y;
      return;
    }
    if (Array.isArray(node)) node.forEach(visit);
  };
  visit(geometry.coordinates);
  if (!Number.isFinite(minX)) return null;
  return [minX, minY, maxX, maxY];
}

interface FieldMapParams {
  field: Field | null;
  index: IndexKey;
  sceneId: string | null;
  showRaster: boolean;
  /** Fit the camera to the field on selection. The follower map in a synced pair sets this false
   *  so the shared-camera controller drives it instead. */
  fit?: boolean;
  controls?: boolean;
  /** Receives the map on creation and `null` on teardown, so a parent can sync several maps. */
  onMap?: (map: MaplibreMap | null) => void;
}

/** Owns one MapLibre map for a field: the boundary outline and the toggled index raster overlay
 *  (one field/scene/geometry-version COG via the tiler). Shared by the single map and the
 *  side-by-side comparison, so the rendering logic lives in exactly one place. */
export function useFieldMap(
  containerRef: RefObject<HTMLDivElement | null>,
  { field, index, sceneId, showRaster, fit = true, controls = true, onMap }: FieldMapParams,
): void {
  const mapRef = useRef<MaplibreMap | null>(null);
  const readyRef = useRef(false);
  const onMapRef = useRef(onMap);
  onMapRef.current = onMap;
  const controlsRef = useRef(controls);
  controlsRef.current = controls;

  // Create the map once. onMap is read through a ref so changing it never re-creates the map.
  useEffect(() => {
    if (!containerRef.current || mapRef.current) return;
    const map = new maplibregl.Map({
      container: containerRef.current,
      style: config.basemapUrl || minimalStyle(),
      center: [30, -18],
      zoom: 5,
      attributionControl: { compact: true },
    });
    if (controlsRef.current) {
      map.addControl(new maplibregl.NavigationControl({ showCompass: false }), "top-right");
    }
    map.on("load", () => {
      readyRef.current = true;
    });
    mapRef.current = map;
    onMapRef.current?.(map);
    return () => {
      readyRef.current = false;
      onMapRef.current?.(null);
      map.remove();
      mapRef.current = null;
    };
  }, [containerRef]);

  // The selected field's geometry as a GeoJSON layer, optionally fitted into view.
  useEffect(() => {
    const map = mapRef.current;
    if (!map) return;
    const apply = () => {
      const geometry = field?.geometry ?? null;
      const data = geometry
        ? { type: "Feature" as const, geometry, properties: {} }
        : { type: "FeatureCollection" as const, features: [] };
      const source = map.getSource(FIELD_SOURCE) as GeoJSONSource | undefined;
      if (source) {
        source.setData(data as GeoJSON.GeoJSON);
      } else {
        map.addSource(FIELD_SOURCE, { type: "geojson", data: data as GeoJSON.GeoJSON });
        map.addLayer({
          id: FIELD_FILL,
          type: "fill",
          source: FIELD_SOURCE,
          paint: { "fill-color": cssVar("--accent", "#5a86e0"), "fill-opacity": 0.12 },
        });
        map.addLayer({
          id: FIELD_LINE,
          type: "line",
          source: FIELD_SOURCE,
          paint: { "line-color": cssVar("--accent", "#5a86e0"), "line-width": 2 },
        });
      }
      if (geometry && fit) {
        const bbox = bboxOf(geometry);
        if (bbox) {
          map.fitBounds(
            [
              [bbox[0], bbox[1]],
              [bbox[2], bbox[3]],
            ],
            { padding: 64, duration: 600, maxZoom: 15 },
          );
        }
      }
    };
    if (readyRef.current) apply();
    else map.once("load", apply);
  }, [field, fit]);

  // The index raster overlay from the tiler, kept beneath the field outline, toggled on demand.
  useEffect(() => {
    const map = mapRef.current;
    if (!map) return;
    const apply = () => {
      if (map.getLayer(INDEX_LAYER)) map.removeLayer(INDEX_LAYER);
      if (map.getSource(INDEX_SOURCE)) map.removeSource(INDEX_SOURCE);
      if (!showRaster || !field || !sceneId) return;
      map.addSource(INDEX_SOURCE, {
        type: "raster",
        tiles: [
          indexTileTemplate({
            index,
            geometryVersion: field.geometry_version,
            fieldId: field.field_id,
            sceneId,
          }),
        ],
        tileSize: 256,
      });
      const beforeId = map.getLayer(FIELD_LINE) ? FIELD_LINE : undefined;
      map.addLayer(
        { id: INDEX_LAYER, type: "raster", source: INDEX_SOURCE, paint: { "raster-opacity": 0.8 } },
        beforeId,
      );
    };
    if (readyRef.current) apply();
    else map.once("load", apply);
  }, [showRaster, index, field, sceneId]);
}
