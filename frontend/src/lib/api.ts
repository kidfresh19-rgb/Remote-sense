import type { Geometry } from "geojson";

import { config } from "./config";

/** Shapes mirror the Pydantic out-schemas in services/api/workspace.py. */
export interface Farm {
  canonical_farm_id: string;
  name: string | null;
  region: string | null;
}

export interface Field {
  field_id: string;
  canonical_field_id: string | null;
  name: string | null;
  crop: string | null;
  geometry_version: number;
  geometry: Geometry;
}

export interface TimeseriesPoint {
  pass_date: string;
  mean: number | null;
  min: number | null;
  max: number | null;
  std: number | null;
  p10: number | null;
  p90: number | null;
  clear_fraction: number;
  confidence: string | null;
}

export interface Scene {
  scene_id: string;
  pass_date: string;
}

export interface Interpretation {
  pass_date: string;
  status: string;
  confidence: string;
  narrative: string;
  published: boolean;
  needs_review: boolean;
}

export interface AuditRecord {
  pass_date: string;
  index_name: string;
  scene_id: string;
  provider: string;
  provider_scene_id: string;
  processing_mode: string;
  formula_version: string;
  geometry_version: number;
  resolution_m: number;
  clear_fraction: number;
  confidence: string | null;
  cog_uri: string | null;
  created_at: string;
}

export interface Annotation {
  id: string;
  field_id: string;
  geometry_version: number;
  pass_date: string | null;
  body: string;
  author: string | null;
  created_at: string;
}

export class ApiError extends Error {
  readonly status: number;
  constructor(status: number, message: string) {
    super(message);
    this.name = "ApiError";
    this.status = status;
  }
  get isAuth(): boolean {
    return this.status === 401 || this.status === 403;
  }
}

async function get<T>(path: string, token: string, signal?: AbortSignal): Promise<T> {
  let resp: Response;
  try {
    resp = await fetch(`${config.apiBaseUrl}${path}`, {
      headers: { Authorization: `Bearer ${token}`, Accept: "application/json" },
      signal,
    });
  } catch {
    throw new ApiError(0, `cannot reach the workspace API at ${config.apiBaseUrl}`);
  }
  if (!resp.ok) {
    const detail = await resp.text().catch(() => "");
    throw new ApiError(resp.status, detail || `request failed (${resp.status})`);
  }
  return (await resp.json()) as T;
}

/** Mutating request (POST/DELETE). 204 responses carry no body, so resolve to undefined. */
async function send<T>(method: string, path: string, token: string, body?: unknown): Promise<T> {
  const hasBody = body !== undefined;
  let resp: Response;
  try {
    resp = await fetch(`${config.apiBaseUrl}${path}`, {
      method,
      headers: {
        Authorization: `Bearer ${token}`,
        Accept: "application/json",
        ...(hasBody ? { "Content-Type": "application/json" } : {}),
      },
      body: hasBody ? JSON.stringify(body) : undefined,
    });
  } catch {
    throw new ApiError(0, `cannot reach the workspace API at ${config.apiBaseUrl}`);
  }
  if (!resp.ok) {
    const detail = await resp.text().catch(() => "");
    throw new ApiError(resp.status, detail || `request failed (${resp.status})`);
  }
  if (resp.status === 204) return undefined as T;
  return (await resp.json()) as T;
}

export const api = {
  farms: (token: string, signal?: AbortSignal) => get<Farm[]>("/farms", token, signal),
  fields: (canonicalFarmId: string, token: string, signal?: AbortSignal) =>
    get<Field[]>(`/farms/${encodeURIComponent(canonicalFarmId)}/fields`, token, signal),
  timeseries: (fieldId: string, index: string, token: string, signal?: AbortSignal) =>
    get<TimeseriesPoint[]>(
      `/fields/${fieldId}/timeseries?index=${encodeURIComponent(index)}`,
      token,
      signal,
    ),
  scenes: (fieldId: string, token: string, signal?: AbortSignal) =>
    get<Scene[]>(`/fields/${fieldId}/scenes`, token, signal),
  interpretations: (fieldId: string, token: string, signal?: AbortSignal) =>
    get<Interpretation[]>(`/fields/${fieldId}/interpretations`, token, signal),
  audit: (fieldId: string, token: string, signal?: AbortSignal) =>
    get<AuditRecord[]>(`/fields/${fieldId}/audit`, token, signal),
  annotations: (fieldId: string, token: string, signal?: AbortSignal) =>
    get<Annotation[]>(`/fields/${fieldId}/annotations`, token, signal),
  addAnnotation: (
    fieldId: string,
    input: { body: string; pass_date: string | null },
    token: string,
  ) => send<Annotation>("POST", `/fields/${fieldId}/annotations`, token, input),
  deleteAnnotation: (fieldId: string, annotationId: string, token: string) =>
    send<void>(
      "DELETE",
      `/fields/${fieldId}/annotations/${encodeURIComponent(annotationId)}`,
      token,
    ),
};

/** XYZ template the MapLibre raster source points at, addressing one field/scene/geometry-version
 *  index COG. The tiler 503s without the raster stack and 404s until that COG is emitted, so the
 *  overlay is empty until the pipeline has stored it. */
export function indexTileTemplate(params: {
  index: string;
  geometryVersion: number;
  fieldId: string;
  sceneId: string;
}): string {
  const { index, geometryVersion, fieldId, sceneId } = params;
  const path = `${encodeURIComponent(index)}/${geometryVersion}/${encodeURIComponent(
    fieldId,
  )}/${encodeURIComponent(sceneId)}/{z}/{x}/{y}.png`;
  return `${config.tilerBaseUrl}/tiles/${path}`;
}
