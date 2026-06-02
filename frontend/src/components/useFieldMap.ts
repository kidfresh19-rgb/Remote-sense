import type { Geometry, Polygon } from "geojson";
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

const CUSTOM_AOI_SOURCE = "custom-aoi";
const CUSTOM_AOI_FILL = "custom-aoi-fill";
const CUSTOM_AOI_LINE = "custom-aoi-line";

const DRAW_VERTS_SOURCE = "draw-verts";
const DRAW_LINE_SOURCE = "draw-line";
const DRAW_VERTS_LAYER = "draw-verts-layer";
const DRAW_LINE_LAYER = "draw-line-layer";

function cssVar(name: string, fallback: string): string {
  const value = getComputedStyle(document.documentElement).getPropertyValue(name).trim();
  return value || fallback;
}

function minimalStyle(): StyleSpecification {
  // Blank offline style: a flat background plus whatever layers we add at runtime, with no external
  // tile dependency. Used only when VITE_BASEMAP_URL is "none" (air-gapped / offline).
  return {
    version: 8,
    sources: {},
    layers: [
      { id: "bg", type: "background", paint: { "background-color": cssVar("--bg", "#0b0b0d") } },
    ],
  };
}

function streetBasemapStyle(): StyleSpecification {
  // Default key-free basemap so entered coordinates / drawn AOIs land on a real map. Carto raster
  // tiles carry OSM data and are built for app basemaps. Dark theme matches the cockpit; swap the
  // path to "rastertiles/voyager" or "light_all" for a light street map. MapLibre's raster `tiles`
  // does not expand {s}, so the subdomains are listed explicitly.
  const path = "dark_all";
  return {
    version: 8,
    sources: {
      basemap: {
        type: "raster",
        tiles: ["a", "b", "c", "d"].map(
          (sub) => `https://${sub}.basemaps.cartocdn.com/${path}/{z}/{x}/{y}.png`,
        ),
        tileSize: 256,
        maxzoom: 20,
        attribution: "© OpenStreetMap contributors © CARTO",
      },
    },
    layers: [
      // Background shows the panel color while tiles load; the basemap raster sits on top of it.
      { id: "bg", type: "background", paint: { "background-color": cssVar("--bg", "#0b0b0d") } },
      { id: "basemap", type: "raster", source: "basemap" },
    ],
  };
}

function resolveStyle(): string | StyleSpecification {
  const url = config.basemapUrl;
  // Explicit escape hatch for offline / air-gapped use: no external tiles at all.
  if (url === "none") return minimalStyle();
  // A configured style URL (vector or raster) always wins.
  if (url) return url;
  // Default: the built-in key-free street basemap.
  return streetBasemapStyle();
}

export function bboxOf(geometry: Geometry): [number, number, number, number] | null {
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
  /** Custom AOI geometry to render as a dashed overlay. */
  customAOI?: Geometry | null;
  /** When true, the map enters polygon draw mode. */
  drawMode?: boolean;
  /** Called with the completed polygon when the user double-clicks to close the ring. */
  onDrawComplete?: (polygon: Polygon) => void;
  /** Called once after the map is ready, providing navigation helpers the caller can invoke. */
  onMapReady?: (
    flyTo: (center: [number, number], zoom?: number) => void,
    fitBounds: (sw: [number, number], ne: [number, number]) => void,
  ) => void;
}

function safeRemoveLayer(map: MaplibreMap, id: string): void {
  if (map.getLayer(id)) map.removeLayer(id);
}

function safeRemoveSource(map: MaplibreMap, id: string): void {
  if (map.getSource(id)) map.removeSource(id);
}

function updateDrawLayers(map: MaplibreMap, verts: [number, number][]): void {
  const accent = cssVar("--accent", "#5a86e0");

  const vertsData: GeoJSON.GeoJSON = {
    type: "FeatureCollection",
    features: verts.map((p) => ({
      type: "Feature",
      geometry: { type: "Point", coordinates: p },
      properties: {},
    })),
  };

  const lineData: GeoJSON.GeoJSON = {
    type: "FeatureCollection",
    features:
      verts.length > 1
        ? [
            {
              type: "Feature",
              geometry: { type: "LineString", coordinates: verts },
              properties: {},
            },
          ]
        : [],
  };

  const vertsSource = map.getSource(DRAW_VERTS_SOURCE) as GeoJSONSource | undefined;
  const lineSource = map.getSource(DRAW_LINE_SOURCE) as GeoJSONSource | undefined;

  if (vertsSource) {
    vertsSource.setData(vertsData);
  } else {
    map.addSource(DRAW_VERTS_SOURCE, { type: "geojson", data: vertsData });
    map.addLayer({
      id: DRAW_VERTS_LAYER,
      type: "circle",
      source: DRAW_VERTS_SOURCE,
      paint: {
        "circle-radius": 5,
        "circle-color": accent,
        "circle-stroke-color": "#ffffff",
        "circle-stroke-width": 1.5,
      },
    });
  }

  if (lineSource) {
    lineSource.setData(lineData);
  } else {
    map.addSource(DRAW_LINE_SOURCE, { type: "geojson", data: lineData });
    map.addLayer(
      {
        id: DRAW_LINE_LAYER,
        type: "line",
        source: DRAW_LINE_SOURCE,
        paint: {
          "line-color": accent,
          "line-width": 2,
          "line-dasharray": [3, 2],
        },
      },
      DRAW_VERTS_LAYER, // keep verts on top of the line
    );
  }
}

function stopDraw(
  map: MaplibreMap | null,
  clickHandlerRef: React.MutableRefObject<((e: maplibregl.MapMouseEvent) => void) | null>,
  dblclickHandlerRef: React.MutableRefObject<((e: maplibregl.MapMouseEvent) => void) | null>,
  drawVerticesRef: React.MutableRefObject<[number, number][]>,
  isDrawingRef: React.MutableRefObject<boolean>,
): void {
  if (!map) return;
  if (clickHandlerRef.current) {
    map.off("click", clickHandlerRef.current);
    clickHandlerRef.current = null;
  }
  if (dblclickHandlerRef.current) {
    map.off("dblclick", dblclickHandlerRef.current);
    dblclickHandlerRef.current = null;
  }
  safeRemoveLayer(map, DRAW_VERTS_LAYER);
  safeRemoveLayer(map, DRAW_LINE_LAYER);
  safeRemoveSource(map, DRAW_VERTS_SOURCE);
  safeRemoveSource(map, DRAW_LINE_SOURCE);
  map.getCanvas().style.cursor = "";
  isDrawingRef.current = false;
  drawVerticesRef.current = [];
}

/** Owns one MapLibre map for a field: the boundary outline and the toggled index raster overlay
 *  (one field/scene/geometry-version COG via the tiler). Shared by the single map and the
 *  side-by-side comparison, so the rendering logic lives in exactly one place. */
export function useFieldMap(
  containerRef: RefObject<HTMLDivElement | null>,
  {
    field,
    index,
    sceneId,
    showRaster,
    fit = true,
    controls = true,
    onMap,
    customAOI,
    drawMode,
    onDrawComplete,
    onMapReady,
  }: FieldMapParams,
): { cancelDraw: () => void } {
  const mapRef = useRef<MaplibreMap | null>(null);
  const readyRef = useRef(false);
  const onMapRef = useRef(onMap);
  onMapRef.current = onMap;
  const controlsRef = useRef(controls);
  controlsRef.current = controls;
  const onMapReadyRef = useRef(onMapReady);
  onMapReadyRef.current = onMapReady;
  const onDrawCompleteRef = useRef(onDrawComplete);
  onDrawCompleteRef.current = onDrawComplete;

  // Draw mode refs — kept as refs (not state) so event handlers always see fresh values without
  // needing to be recreated on every vertex addition.
  const drawVerticesRef = useRef<[number, number][]>([]);
  const isDrawingRef = useRef(false);
  const clickHandlerRef = useRef<((e: maplibregl.MapMouseEvent) => void) | null>(null);
  const dblclickHandlerRef = useRef<((e: maplibregl.MapMouseEvent) => void) | null>(null);

  // Create the map once. onMap is read through a ref so changing it never re-creates the map.
  useEffect(() => {
    if (!containerRef.current || mapRef.current) return;
    const map = new maplibregl.Map({
      container: containerRef.current,
      style: resolveStyle(),
      center: [30, -18],
      zoom: 5,
      attributionControl: { compact: true },
    });
    if (controlsRef.current) {
      map.addControl(new maplibregl.NavigationControl({ showCompass: false }), "top-right");
    }
    map.on("load", () => {
      readyRef.current = true;
      onMapReadyRef.current?.(
        (center, zoom) => map.flyTo({ center, zoom: zoom ?? 13, duration: 1000 }),
        (sw, ne) => map.fitBounds([sw, ne], { padding: 64, duration: 800, maxZoom: 15 }),
      );
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

  // Custom AOI overlay: dashed line + translucent fill, placed beneath the field outline.
  useEffect(() => {
    const map = mapRef.current;
    if (!map) return;
    const apply = () => {
      safeRemoveLayer(map, CUSTOM_AOI_FILL);
      safeRemoveLayer(map, CUSTOM_AOI_LINE);
      safeRemoveSource(map, CUSTOM_AOI_SOURCE);

      if (!customAOI) return;

      const accent = cssVar("--accent", "#5a86e0");
      map.addSource(CUSTOM_AOI_SOURCE, {
        type: "geojson",
        data: { type: "Feature", geometry: customAOI, properties: {} },
      });

      // Fill placed before FIELD_LINE so the field outline remains on top.
      const beforeId = map.getLayer(FIELD_LINE) ? FIELD_LINE : undefined;
      map.addLayer(
        {
          id: CUSTOM_AOI_FILL,
          type: "fill",
          source: CUSTOM_AOI_SOURCE,
          paint: { "fill-color": accent, "fill-opacity": 0.1 },
        },
        beforeId,
      );
      map.addLayer(
        {
          id: CUSTOM_AOI_LINE,
          type: "line",
          source: CUSTOM_AOI_SOURCE,
          paint: { "line-color": accent, "line-width": 2, "line-dasharray": [4, 3] },
        },
        beforeId,
      );

      // Fit to AOI only when no field is selected — field selection has its own fit logic.
      if (!field) {
        const bbox = bboxOf(customAOI);
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
  }, [customAOI, field]);

  // Draw mode: click to place vertices, double-click to close the polygon.
  useEffect(() => {
    const map = mapRef.current;
    if (!map) return;

    const startDraw = () => {
      isDrawingRef.current = true;
      drawVerticesRef.current = [];
      map.getCanvas().style.cursor = "crosshair";

      const onClick = (e: maplibregl.MapMouseEvent) => {
        if (!isDrawingRef.current) return;
        drawVerticesRef.current = [
          ...drawVerticesRef.current,
          [e.lngLat.lng, e.lngLat.lat],
        ];
        updateDrawLayers(map, drawVerticesRef.current);
      };

      const onDblclick = (e: maplibregl.MapMouseEvent) => {
        // Prevent the map's default zoom-on-dblclick behaviour.
        e.preventDefault();
        if (!isDrawingRef.current) return;
        // The click handler fires before dblclick on the final point — pop that extra vertex.
        const verts = drawVerticesRef.current.slice(0, -1);
        if (verts.length < 3) return;
        // Close the ring by duplicating the first vertex at the end.
        const ring: [number, number][] = [...verts, verts[0]];
        const polygon: Polygon = { type: "Polygon", coordinates: [ring] };
        onDrawCompleteRef.current?.(polygon);
        stopDraw(map, clickHandlerRef, dblclickHandlerRef, drawVerticesRef, isDrawingRef);
      };

      clickHandlerRef.current = onClick;
      dblclickHandlerRef.current = onDblclick;
      map.on("click", onClick);
      map.on("dblclick", onDblclick);
    };

    if (drawMode) {
      if (readyRef.current) startDraw();
      else map.once("load", startDraw);
    } else {
      stopDraw(map, clickHandlerRef, dblclickHandlerRef, drawVerticesRef, isDrawingRef);
    }

    return () => {
      stopDraw(map, clickHandlerRef, dblclickHandlerRef, drawVerticesRef, isDrawingRef);
    };
  }, [drawMode]);

  const cancelDraw = () => {
    stopDraw(mapRef.current, clickHandlerRef, dblclickHandlerRef, drawVerticesRef, isDrawingRef);
  };

  return { cancelDraw };
}
