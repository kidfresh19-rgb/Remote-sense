import type { Geometry } from "geojson";

export interface GeocodingResult {
  place_id: number;
  display_name: string;
  lat: string;
  lon: string;
  geojson?: Geometry;
  type: string;
  importance: number;
}

// Nominatim JSON response shape (only the fields we care about).
interface NominatimItem {
  place_id: number;
  display_name: string;
  lat: string;
  lon: string;
  geojson?: unknown;
  type: string;
  importance: number;
}

const BASE = "https://nominatim.openstreetmap.org/search";

/** Query Nominatim for place suggestions. Returns [] for an empty query or a cancelled request;
 *  throws on a real network/HTTP failure so the caller can tell "no matches" from "search failed".
 *  Always pass an AbortSignal so the caller can cancel in-flight requests on new keystrokes. */
export async function geocodeSearch(
  query: string,
  signal?: AbortSignal,
): Promise<GeocodingResult[]> {
  const q = query.trim();
  if (!q) return [];

  const params = new URLSearchParams({
    q,
    format: "json",
    limit: "6",
    polygon_geojson: "1",
    addressdetails: "0",
  });

  try {
    const res = await fetch(`${BASE}?${params.toString()}`, {
      signal,
      // Nominatim identifies a browser caller by the Referer/Origin the browser sends
      // automatically. User-Agent is a forbidden fetch header and cannot be set here.
      headers: { Accept: "application/json" },
    });
    if (!res.ok) throw new Error(`geocoding request failed (${res.status})`);
    const raw = (await res.json()) as NominatimItem[];
    return raw.map((item) => ({
      place_id: item.place_id,
      display_name: item.display_name,
      lat: item.lat,
      lon: item.lon,
      // Only carry the geometry when it is a valid GeoJSON Geometry object.
      geojson:
        item.geojson && typeof item.geojson === "object" && "type" in (item.geojson as object)
          ? (item.geojson as Geometry)
          : undefined,
      type: item.type,
      importance: item.importance,
    }));
  } catch (err) {
    // AbortError is a normal cancellation — do not surface it as a failure.
    if (err instanceof DOMException && err.name === "AbortError") return [];
    throw err instanceof Error ? err : new Error("geocoding failed");
  }
}
