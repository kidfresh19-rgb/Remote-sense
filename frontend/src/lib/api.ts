import type { Geometry } from "geojson";

import { config } from "./config";

/** Shapes mirror the Pydantic out-schemas in services/api/workspace.py. */
export interface Farm {
  canonical_farm_id: string;
  name: string | null;
  region: string | null;
  overall_health: string | null;
  overall_health_score: number | null;
  latest_pass_date: string | null;
  total_fields: number | null;
  total_area_hectares: number | null;
  crops: string[] | null;
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
  id: string;
  pass_date: string;
  status: string;
  confidence: string;
  narrative: string;
  published: boolean;
  needs_review: boolean;
  reviewed_by: string | null;
  reviewed_at: string | null;
}

/** An agronomist's review action: publish/withhold and optionally correct the narrative. A null or
 *  omitted narrative keeps the drafted text; status/confidence are grounded in the numbers and are
 *  not editable. Mirrors InterpretationReview in services/api/workspace.py. */
export interface ReviewInput {
  publish: boolean;
  narrative?: string | null;
}

/** A row of the cross-field review queue (GET /interpretations/review-queue). Geometry-free. */
export interface ReviewQueueItem {
  id: string;
  field_id: string;
  canonical_field_id: string | null;
  canonical_farm_id: string;
  field_name: string | null;
  crop: string | null;
  pass_date: string;
  status: string;
  confidence: string;
  narrative: string;
  published: boolean;
  needs_review: boolean;
  reviewed_by: string | null;
  reviewed_at: string | null;
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

/** Result of an ad-hoc AOI preview analysis (POST /analyse/aoi). `status` is "ok" or "no_scenes";
 *  on "ok" the index stats for the most recent usable pass are present. Mirrors the worker task
 *  `analysis.analyse_aoi` return shape. Nothing is persisted - this is a quick look, not a field. */
export interface AOIAnalysisResult {
  status: "ok" | "no_scenes" | string;
  index?: string;
  pass_date?: string;
  scene_id?: string;
  mean?: number | null;
  min?: number | null;
  max?: number | null;
  p10?: number | null;
  p90?: number | null;
  clear_fraction?: number;
  confidence?: string;
  resolution_m?: number;
  pixels?: number;
}

export interface PublishStatus {
  canonical_farm_id: string;
  status: "pending" | "published" | "dead_letter" | string;
  result_count: number;
  pushed_at: string | null;
  last_error: string | null;
  /** Active gateway adapter: "recording" | "http" | "agritrack". */
  gateway: string;
  /** True when the gateway records "published" without sending (the recording dry-run sink). */
  dry_run: boolean;
}

/** Response of POST /farms/{id}/publish: the push was accepted onto the queue. `dry_run` tells the
 *  workspace whether this will actually reach the gateway or is a recording no-op. */
export interface PublishEnqueued {
  status: string;
  canonical_farm_id: string;
  by: string;
  gateway: string;
  dry_run: boolean;
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
  reviewInterpretation: (
    fieldId: string,
    interpretationId: string,
    input: ReviewInput,
    token: string,
  ) =>
    send<Interpretation>(
      "PATCH",
      `/fields/${fieldId}/interpretations/${encodeURIComponent(interpretationId)}`,
      token,
      input,
    ),
  reviewQueue: (needsReview: boolean | undefined, token: string, signal?: AbortSignal) =>
    get<ReviewQueueItem[]>(
      `/interpretations/review-queue${needsReview ? "?needs_review=true" : ""}`,
      token,
      signal,
    ),
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
  analyseAOI: (geometry: Geometry, index: string, token: string) =>
    send<AOIAnalysisResult>("POST", "/analyse/aoi", token, { geometry, index }),
  collectField: (fieldId: string, token: string) =>
    send<{ status: string; field_id: string; by: string }>(
      "POST",
      `/fields/${fieldId}/collect`,
      token,
    ),
  publishFarm: (canonicalFarmId: string, token: string) =>
    send<PublishEnqueued>(
      "POST",
      `/farms/${encodeURIComponent(canonicalFarmId)}/publish`,
      token,
    ),
  publishStatus: (canonicalFarmId: string, token: string, signal?: AbortSignal) =>
    get<PublishStatus>(
      `/farms/${encodeURIComponent(canonicalFarmId)}/publish/status`,
      token,
      signal,
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
