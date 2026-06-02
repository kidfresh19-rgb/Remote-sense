import type { Feature, FeatureCollection, Geometry } from "geojson";

export type ParseResult =
  | { ok: true; geometry: Geometry; label: string }
  | { ok: false; error: string };

/** Extract a single Geometry from a parsed GeoJSON value. Accepts Feature, FeatureCollection
 *  (uses the first feature), or a raw Geometry. Returns null when none match. */
function extractGeometry(value: unknown): Geometry | null {
  if (!value || typeof value !== "object") return null;
  const obj = value as Record<string, unknown>;
  if (typeof obj.type !== "string") return null;

  if (obj.type === "Feature") {
    const feat = obj as unknown as Feature;
    return feat.geometry ?? null;
  }
  if (obj.type === "FeatureCollection") {
    const coll = obj as unknown as FeatureCollection;
    if (!coll.features.length) return null;
    return coll.features[0].geometry ?? null;
  }
  // Assume it is a raw Geometry if it has a type and coordinates.
  if ("coordinates" in obj || obj.type === "GeometryCollection") {
    return obj as unknown as Geometry;
  }
  return null;
}

function stripExtension(name: string): string {
  return name.replace(/\.[^.]+$/, "");
}

/** Parse a user-supplied file into a GeoJSON Geometry for use as a custom AOI.
 *  Supports .geojson/.json and .zip (shapefile). */
export async function parseAOIFile(file: File): Promise<ParseResult> {
  const name = file.name.toLowerCase();
  const label = stripExtension(file.name);

  if (name.endsWith(".geojson") || name.endsWith(".json")) {
    try {
      const text = await file.text();
      const parsed: unknown = JSON.parse(text);
      const geometry = extractGeometry(parsed);
      if (!geometry) {
        return {
          ok: false,
          error: "File must contain a GeoJSON Feature, FeatureCollection, or Geometry.",
        };
      }
      return { ok: true, geometry, label };
    } catch {
      return { ok: false, error: "Invalid JSON. The file could not be parsed." };
    }
  }

  if (name.endsWith(".zip")) {
    try {
      // Dynamic import keeps shpjs out of the initial bundle.
      const shp = await import("shpjs");
      const buffer = await file.arrayBuffer();
      // shpjs can return a FeatureCollection or an array of FeatureCollections.
      const result = await shp.default(buffer);

      let geometry: Geometry | null = null;

      if (Array.isArray(result)) {
        const first = result[0];
        if (first && first.features.length) {
          geometry = first.features[0].geometry ?? null;
        }
      } else {
        // Single FeatureCollection
        if (result.features.length) {
          geometry = result.features[0].geometry ?? null;
        }
      }

      if (!geometry) {
        return { ok: false, error: "No features found in the shapefile." };
      }
      return { ok: true, geometry, label };
    } catch {
      return {
        ok: false,
        error:
          "Failed to parse the shapefile. Ensure the zip contains .shp, .dbf, and .prj files.",
      };
    }
  }

  return {
    ok: false,
    error: "Unsupported file type. Use .geojson, .json, or .zip (shapefile).",
  };
}
