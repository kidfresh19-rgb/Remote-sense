import type { Geometry, Polygon } from "geojson";
import maplibregl, {
  type GeoJSONSource,
  type Map as MaplibreMap,
  type StyleSpecification,
} from "maplibre-gl";
import { useEffect, useRef, useState, type RefObject } from "react";

import { diffTileTemplate, indexTileTemplate, maskTileTemplate, type Field } from "@/lib/api";
import { config } from "@/lib/config";
import { activeRasterKey, type IndexKey } from "@/lib/indices";

const FIELD_SOURCE = "field";
const FIELD_FILL = "field-fill";
const FIELD_LINE = "field-line";
const INDEX_SOURCE = "index-raster";
const INDEX_LAYER = "index-raster-layer";
// Cloud-mask honesty overlay (backlog 0046): a semi-transparent hatch marking pixels the per-AOI
// SCL mask dropped for the active pass. One mask per pass, independent of INDEX_LAYER's index.
const MASK_SOURCE = "cloud-mask-raster";
const MASK_LAYER = "cloud-mask-raster-layer";
// Pass-to-pass difference layer (backlog 0045): index(sceneB) minus index(sceneA) between two
// explicit passes. Stands in for the plain index/rgb/fcc overlay - the diff pane's caller passes
// showRaster/showRgb/showFcc all false, so INDEX_LAYER never competes with this one for the same
// pane.
const DIFF_SOURCE = "index-diff-raster";
const DIFF_LAYER = "index-diff-raster-layer";

const CUSTOM_AOI_SOURCE = "custom-aoi";
const CUSTOM_AOI_FILL = "custom-aoi-fill";
const CUSTOM_AOI_LINE = "custom-aoi-line";

// Region-boundary context overlays (comparison groups, PRD 0002 slices 8a/8b): the seeded Natural
// Region layer and a chosen analyst-uploaded layer, each a toggled GeoJSON fill+line beneath the
// field. Distinct hues so the two read apart from each other and from the field accent.
const NR_SOURCE = "region-nr";
const NR_FILL = "region-nr-fill";
const NR_LINE = "region-nr-line";
const NR_COLOR = "#f0b429"; // amber: seeded Natural Region boundaries
const UPLOADED_SOURCE = "region-uploaded";
const UPLOADED_FILL = "region-uploaded-fill";
const UPLOADED_LINE = "region-uploaded-line";
const UPLOADED_COLOR = "#9b8afb"; // violet: analyst-uploaded boundaries

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

function satelliteBasemapStyle(): StyleSpecification {
  // Default key-free basemap for a satellite-agri product: flying to a field has to show the actual
  // land, not a near-black street map. Esri World Imagery supplies the imagery; a Carto label-only
  // raster sits on top for town/road names so the analyst can orient. Esri imagery tiles are
  // addressed {z}/{y}/{x} (note the y/x order); Carto tiles are {z}/{x}/{y} with no {s} expansion in
  // MapLibre, so the subdomains are listed explicitly. Override with VITE_BASEMAP_URL, or set it to
  // "none" for an offline blank style.
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
          (sub) => `https://${sub}.basemaps.cartocdn.com/dark_only_labels/{z}/{x}/{y}.png`,
        ),
        tileSize: 256,
        maxzoom: 20,
        attribution: "© OpenStreetMap contributors © CARTO",
      },
    },
    layers: [
      // Background shows the panel color while imagery loads; imagery then labels sit on top.
      { id: "bg", type: "background", paint: { "background-color": cssVar("--bg", "#0b0b0d") } },
      { id: "satellite", type: "raster", source: "satellite" },
      { id: "labels", type: "raster", source: "labels", paint: { "raster-opacity": 0.9 } },
    ],
  };
}

function resolveStyle(): string | StyleSpecification {
  const url = config.basemapUrl;
  // Explicit escape hatch for offline / air-gapped use: no external tiles at all.
  if (url === "none") return minimalStyle();
  // A configured style URL (vector or raster) always wins.
  if (url) return url;
  // Default: the built-in key-free satellite basemap with place labels.
  return satelliteBasemapStyle();
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
  showRgb: boolean;
  /** False color (NIR/red/green). Optional so the comparison maps, which are index-only, need
   *  no change. */
  showFcc?: boolean;
  /** Cloud-mask honesty overlay (backlog 0046): a semi-transparent hatch over pixels the per-AOI
   *  SCL mask dropped for the active pass. Independent of showRaster/showRgb/showFcc - it renders
   *  above whichever of those is on, so it carries no `index` and never refetches or flickers
   *  when the analyst switches the displayed index. */
  showCloudMask: boolean;
  /** Pass-to-pass difference layer (backlog 0045): non-null renders index(sceneB) minus
   *  index(sceneA) for `index`, replacing the plain index/rgb/fcc overlay for this map (the caller
   *  passes showRaster/showRgb/showFcc all false alongside it). Null/undefined removes the layer.
   *  Never computed here - the caller resolves both scene ids from its own explicit A/B pick. */
  diffPass?: { sceneA: string; sceneB: string } | null;
  /** Fit the camera to the field on selection. The follower map in a synced pair sets this false
   *  so the shared-camera controller drives it instead. */
  fit?: boolean;
  controls?: boolean;
  /** Receives the map on creation and `null` on teardown, so a parent can sync several maps. */
  onMap?: (map: MaplibreMap | null) => void;
  /** Custom AOI geometry to render as a dashed overlay. */
  customAOI?: Geometry | null;
  /** Seeded Natural Region boundaries as a GeoJSON FeatureCollection, drawn as a toggled context
   *  overlay. Null/undefined removes the overlay. */
  naturalRegions?: GeoJSON.FeatureCollection | null;
  /** A chosen analyst-uploaded region layer as a GeoJSON FeatureCollection, drawn as a toggled
   *  context overlay. Null/undefined removes the overlay. */
  uploadedRegions?: GeoJSON.FeatureCollection | null;
  /** A point pin ([lng, lat]) for a searched place that has no boundary, so "fly to" lands on a
   *  visible target. Cleared by the caller once a field or AOI boundary takes over. */
  marker?: [number, number] | null;
  /** When true, the map enters polygon draw mode. */
  drawMode?: boolean;
  /** Called with the completed polygon when the user closes the ring (double-click or Enter). */
  onDrawComplete?: (polygon: Polygon) => void;
  /** Called when the user cancels an in-progress draw (Escape) so the parent can exit draw mode. */
  onDrawCancel?: () => void;
  /** Called once after the map is ready, providing navigation helpers the caller can invoke. */
  onMapReady?: (
    flyTo: (center: [number, number], zoom?: number) => void,
    fitBounds: (sw: [number, number], ne: [number, number]) => void,
  ) => void;
}

function safeRemoveLayer(map: MaplibreMap | null, id: string): void {
  if (!map) return;
  try {
    const layer = map.getLayer(id);
    if (layer) map.removeLayer(id);
  } catch {
    // Map may have been destroyed; ignore errors
  }
}

function safeRemoveSource(map: MaplibreMap | null, id: string): void {
  if (!map) return;
  try {
    const source = map.getSource(id);
    if (source) map.removeSource(id);
  } catch {
    // Map may have been destroyed; ignore errors
  }
}

/** Render (or clear) one region-boundary context overlay: a translucent fill + a solid outline
 *  fed by a GeoJSON FeatureCollection. Kept beneath the index raster and the field outline so it
 *  never obscures the analysis. Idempotent: re-applies by updating the source if it already exists,
 *  removing everything when `data` is null/empty. */
function applyBoundaryOverlay(
  map: MaplibreMap,
  ids: { source: string; fill: string; line: string },
  data: GeoJSON.FeatureCollection | null | undefined,
  color: string,
): void {
  if (!data || data.features.length === 0) {
    safeRemoveLayer(map, ids.fill);
    safeRemoveLayer(map, ids.line);
    safeRemoveSource(map, ids.source);
    return;
  }
  const source = map.getSource(ids.source) as GeoJSONSource | undefined;
  if (source) {
    source.setData(data);
    return;
  }
  map.addSource(ids.source, { type: "geojson", data });
  // Sit below the index raster (if shown), else below the field fill, so analysis stays on top.
  const beforeId = map.getLayer(INDEX_LAYER)
    ? INDEX_LAYER
    : map.getLayer(FIELD_FILL)
      ? FIELD_FILL
      : undefined;
  map.addLayer(
    {
      id: ids.fill,
      type: "fill",
      source: ids.source,
      paint: { "fill-color": color, "fill-opacity": 0.07 },
    },
    beforeId,
  );
  map.addLayer(
    {
      id: ids.line,
      type: "line",
      source: ids.source,
      paint: { "line-color": color, "line-width": 1.5 },
    },
    beforeId,
  );
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
  keyHandlerRef: React.MutableRefObject<((e: KeyboardEvent) => void) | null>,
  drawVerticesRef: React.MutableRefObject<[number, number][]>,
  isDrawingRef: React.MutableRefObject<boolean>,
): void {
  // The key listener lives on window, so detach it even if the map is already gone.
  if (keyHandlerRef.current) {
    window.removeEventListener("keydown", keyHandlerRef.current);
    keyHandlerRef.current = null;
  }
  if (map) {
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
  }
  isDrawingRef.current = false;
  drawVerticesRef.current = [];
}

/** Owns one MapLibre map for a field: the boundary outline and the toggled index raster overlay
 *  (one field/scene/geometry-version COG via the tiler). Shared by the single map and the
 *  side-by-side comparison, so the rendering logic lives in exactly one place. Returns
 *  `isRasterLoading` alongside `cancelDraw` - callers that don't need it (e.g. SceneCompare) can
 *  simply ignore it. */
export function useFieldMap(
  containerRef: RefObject<HTMLDivElement | null>,
  {
    field,
    index,
    sceneId,
    showRaster,
    showRgb,
    showFcc = false,
    showCloudMask,
    diffPass,
    fit = true,
    controls = true,
    onMap,
    customAOI,
    naturalRegions,
    uploadedRegions,
    marker,
    drawMode,
    onDrawComplete,
    onDrawCancel,
    onMapReady,
  }: FieldMapParams,
): { cancelDraw: () => void; isRasterLoading: boolean } {
  const mapRef = useRef<MaplibreMap | null>(null);
  const markerRef = useRef<maplibregl.Marker | null>(null);
  const readyRef = useRef(false);
  // True while the index/RGB/FCC raster just requested from the tiler hasn't finished loading -
  // driven by the map's own 'idle' event (fires once every source has settled), against the
  // source swap this hook already performs below. Exposed so a caller (timelapse playback,
  // backlog 0043) can hold its current frame instead of advancing past a tile that hasn't arrived.
  const [isRasterLoading, setIsRasterLoading] = useState(false);
  const onMapRef = useRef(onMap);
  onMapRef.current = onMap;
  const controlsRef = useRef(controls);
  controlsRef.current = controls;
  const onMapReadyRef = useRef(onMapReady);
  onMapReadyRef.current = onMapReady;
  const onDrawCompleteRef = useRef(onDrawComplete);
  onDrawCompleteRef.current = onDrawComplete;
  const onDrawCancelRef = useRef(onDrawCancel);
  onDrawCancelRef.current = onDrawCancel;

  // Draw mode refs — kept as refs (not state) so event handlers always see fresh values without
  // needing to be recreated on every vertex addition.
  const drawVerticesRef = useRef<[number, number][]>([]);
  const isDrawingRef = useRef(false);
  const clickHandlerRef = useRef<((e: maplibregl.MapMouseEvent) => void) | null>(null);
  const dblclickHandlerRef = useRef<((e: maplibregl.MapMouseEvent) => void) | null>(null);
  const keyHandlerRef = useRef<((e: KeyboardEvent) => void) | null>(null);

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
    // Registered once for the map's lifetime (not tied to any one effect's own dependencies):
    // 'idle' fires whenever the map has finished rendering and every source has settled, which is
    // the general "nothing is still loading" signal - simpler and sufficient versus tracking
    // INDEX_SOURCE's individual tile requests, since a raster source swap is the dominant thing
    // that makes the map non-idle while a field is selected.
    map.on("idle", () => setIsRasterLoading(false));
    mapRef.current = map;
    onMapRef.current?.(map);
    return () => {
      readyRef.current = false;
      onMapRef.current?.(null);
      markerRef.current?.remove();
      markerRef.current = null;
      map.remove();
      mapRef.current = null;
    };
  }, [containerRef]);

  // Search pin for a point result (a place with no polygon boundary). Lives outside the style, so it
  // survives basemap changes and needs no add/remove of sources or layers.
  useEffect(() => {
    const map = mapRef.current;
    if (!map) return;
    if (!marker) {
      markerRef.current?.remove();
      markerRef.current = null;
      return;
    }
    if (markerRef.current) {
      markerRef.current.setLngLat(marker);
    } else {
      markerRef.current = new maplibregl.Marker({ color: cssVar("--accent", "#5a86e0") })
        .setLngLat(marker)
        .addTo(map);
    }
  }, [marker]);

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
      // The fcc/rgb/index precedence itself is shared (activeRasterKey); the extra showRaster
      // gate is local to the live map, which - unlike a thumbnail - can hide the layer entirely
      // instead of always falling back to the index colormap.
      const activeRaster =
        showFcc || showRgb || showRaster ? activeRasterKey(index, showRgb, showFcc) : null;
      if (!activeRaster || !field || !sceneId) {
        setIsRasterLoading(false); // nothing requested - nothing to wait for
        return;
      }
      map.addSource(INDEX_SOURCE, {
        type: "raster",
        tiles: [
          indexTileTemplate({
            index: activeRaster,
            geometryVersion: field.geometry_version,
            fieldId: field.field_id,
            sceneId,
          }),
        ],
        tileSize: 256,
      });
      // The cloud-mask overlay (if on) must always render above this layer - checking for
      // MASK_LAYER first, not just FIELD_LINE, keeps that true no matter which of the two the
      // analyst toggled on more recently. Two effects both inserting at a fixed FIELD_LINE anchor
      // would otherwise race: MapLibre's addLayer(layer, beforeId) splices at beforeId's *current*
      // array index, so whichever layer was (re)added most recently ends up on top.
      const beforeId = map.getLayer(MASK_LAYER)
        ? MASK_LAYER
        : map.getLayer(FIELD_LINE)
          ? FIELD_LINE
          : undefined;
      map.addLayer(
        { id: INDEX_LAYER, type: "raster", source: INDEX_SOURCE, paint: { "raster-opacity": 0.8 } },
        beforeId,
      );
      // This source swap just kicked off a new tile fetch; the persistent 'idle' listener
      // registered at map creation flips this back to false once the map settles.
      setIsRasterLoading(true);
    };
    if (readyRef.current) apply();
    else map.once("load", apply);
  }, [showRaster, showRgb, showFcc, index, field, sceneId]);

  // The cloud-mask honesty overlay from the tiler (backlog 0046): a semi-transparent hatch over
  // pixels the per-AOI SCL mask dropped for the active pass. Kept in its own effect, independent
  // of index/rgb/fcc, so switching the displayed base layer never retriggers this fetch or
  // flickers the hatch. beforeId anchors to FIELD_LINE, same as the index overlay above - see that
  // effect's beforeId, which yields to MASK_LAYER when present, so the hatch stays on top of the
  // base layer regardless of which of the two was toggled on more recently.
  useEffect(() => {
    const map = mapRef.current;
    if (!map) return;
    const apply = () => {
      if (map.getLayer(MASK_LAYER)) map.removeLayer(MASK_LAYER);
      if (map.getSource(MASK_SOURCE)) map.removeSource(MASK_SOURCE);
      if (!showCloudMask || !field || !sceneId) return;
      map.addSource(MASK_SOURCE, {
        type: "raster",
        tiles: [
          maskTileTemplate({
            geometryVersion: field.geometry_version,
            fieldId: field.field_id,
            sceneId,
          }),
        ],
        tileSize: 256,
      });
      const beforeId = map.getLayer(FIELD_LINE) ? FIELD_LINE : undefined;
      map.addLayer({ id: MASK_LAYER, type: "raster", source: MASK_SOURCE }, beforeId);
    };
    if (readyRef.current) apply();
    else map.once("load", apply);
  }, [showCloudMask, field, sceneId]);

  // The pass-to-pass difference overlay from the tiler (backlog 0045): index(sceneB) minus
  // index(sceneA) between two explicit passes. In practice mutually exclusive with the plain
  // index/rgb/fcc overlay - the diff pane's caller passes showRaster/showRgb/showFcc all false -
  // so no layering order needs negotiating against INDEX_LAYER; anchored at FIELD_LINE the same
  // way that overlay is.
  useEffect(() => {
    const map = mapRef.current;
    if (!map) return;
    const apply = () => {
      if (map.getLayer(DIFF_LAYER)) map.removeLayer(DIFF_LAYER);
      if (map.getSource(DIFF_SOURCE)) map.removeSource(DIFF_SOURCE);
      if (!diffPass || !field) return;
      map.addSource(DIFF_SOURCE, {
        type: "raster",
        tiles: [
          diffTileTemplate({
            index,
            geometryVersion: field.geometry_version,
            fieldId: field.field_id,
            sceneA: diffPass.sceneA,
            sceneB: diffPass.sceneB,
          }),
        ],
        tileSize: 256,
      });
      const beforeId = map.getLayer(FIELD_LINE) ? FIELD_LINE : undefined;
      map.addLayer(
        { id: DIFF_LAYER, type: "raster", source: DIFF_SOURCE, paint: { "raster-opacity": 0.8 } },
        beforeId,
      );
    };
    if (readyRef.current) apply();
    else map.once("load", apply);
  }, [diffPass, index, field]);

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

  // Natural Region boundary overlay (PRD 0002 slice 8a), toggled and drawn beneath the analysis.
  useEffect(() => {
    const map = mapRef.current;
    if (!map) return;
    const apply = () => applyBoundaryOverlay(map, { source: NR_SOURCE, fill: NR_FILL, line: NR_LINE }, naturalRegions, NR_COLOR);
    if (readyRef.current) apply();
    else map.once("load", apply);
  }, [naturalRegions]);

  // Analyst-uploaded boundary overlay (PRD 0002 slice 8b), toggled and drawn beneath the analysis.
  useEffect(() => {
    const map = mapRef.current;
    if (!map) return;
    const apply = () =>
      applyBoundaryOverlay(
        map,
        { source: UPLOADED_SOURCE, fill: UPLOADED_FILL, line: UPLOADED_LINE },
        uploadedRegions,
        UPLOADED_COLOR,
      );
    if (readyRef.current) apply();
    else map.once("load", apply);
  }, [uploadedRegions]);

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

      const finishRing = (verts: [number, number][]) => {
        if (verts.length < 3) return;
        // Close the ring by duplicating the first vertex at the end.
        const ring: [number, number][] = [...verts, verts[0]];
        onDrawCompleteRef.current?.({ type: "Polygon", coordinates: [ring] });
        stopDraw(map, clickHandlerRef, dblclickHandlerRef, keyHandlerRef, drawVerticesRef, isDrawingRef);
      };

      const onDblclick = (e: maplibregl.MapMouseEvent) => {
        // Prevent the map's default zoom-on-dblclick behaviour.
        e.preventDefault();
        if (!isDrawingRef.current) return;
        // The click handler fires before dblclick on the final point — pop that extra vertex.
        finishRing(drawVerticesRef.current.slice(0, -1));
      };

      const onKeyDown = (e: KeyboardEvent) => {
        if (!isDrawingRef.current) return;
        if (e.key === "Enter") {
          // Finish on Enter: unlike dblclick there is no trailing click vertex to drop.
          e.preventDefault();
          finishRing(drawVerticesRef.current);
        } else if (e.key === "Escape") {
          e.preventDefault();
          stopDraw(map, clickHandlerRef, dblclickHandlerRef, keyHandlerRef, drawVerticesRef, isDrawingRef);
          onDrawCancelRef.current?.();
        } else if (e.key === "Backspace" || e.key === "Delete") {
          // Undo the last placed vertex.
          e.preventDefault();
          drawVerticesRef.current = drawVerticesRef.current.slice(0, -1);
          updateDrawLayers(map, drawVerticesRef.current);
        }
      };

      clickHandlerRef.current = onClick;
      dblclickHandlerRef.current = onDblclick;
      keyHandlerRef.current = onKeyDown;
      map.on("click", onClick);
      map.on("dblclick", onDblclick);
      window.addEventListener("keydown", onKeyDown);
    };

    if (drawMode) {
      if (readyRef.current) startDraw();
      else map.once("load", startDraw);
    } else {
      stopDraw(map, clickHandlerRef, dblclickHandlerRef, keyHandlerRef, drawVerticesRef, isDrawingRef);
    }

    return () => {
      stopDraw(map, clickHandlerRef, dblclickHandlerRef, keyHandlerRef, drawVerticesRef, isDrawingRef);
    };
  }, [drawMode]);

  const cancelDraw = () => {
    stopDraw(mapRef.current, clickHandlerRef, dblclickHandlerRef, keyHandlerRef, drawVerticesRef, isDrawingRef);
  };

  return { cancelDraw, isRasterLoading };
}
