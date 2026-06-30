import type { FeatureCollection, Geometry } from "geojson";

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
  clear_fraction: number;
}

/** One usable pass resolved from an as-of-date request (GET /fields/{id}/as-of). `day_gap` is
 *  signed days from the requested date: zero or negative = on/before, positive = after. */
export interface ResolvedPass {
  scene_id: string;
  pass_date: string;
  day_gap: number;
  clear_fraction: number;
}

/** An arbitrary calendar date resolved against a field's stored passes. Mirrors AsOfResolution
 *  in services/api/workspace/fields.py: the nearest usable pass on each side plus the policy's
 *  pick; every slot is null when no stored pass qualifies (nothing is ever fabricated). */
export interface AsOfResolution {
  requested_date: string;
  index: string;
  min_clear: number;
  before: ResolvedPass | null;
  after: ResolvedPass | null;
  resolved: ResolvedPass | null;
}

export interface RecentActivity {
  date: string;
  activity: string;
  detail: string | null;
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
  gdd_accumulation?: number | null;
  total_precipitation?: number | null;
  recent_activities?: RecentActivity[] | null;
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

export type AOISeriesMode = "dates" | "backfill";

/** A multi-pass AOI preview request (POST /analyse/aoi/series). `dates` (ISO YYYY-MM-DD) drives
 *  the "dates" mode; `months` drives the "backfill" sweep. Provide either a single `index` or a
 *  non-empty `indices` list (the all-indices path, ADR 0011 Phase 2), never both. Mirrors
 *  AOISeriesRequest in services/api/workspace/analyse.py. Nothing is persisted - a preview, not a
 *  field. */
export interface AOISeriesRequest {
  geometry: Geometry;
  index?: string;
  indices?: string[];
  mode: AOISeriesMode;
  dates?: string[];
  months?: number;
}

/** One pass of a multi-pass AOI preview. `requested_date` is present in dates mode (the calendar
 *  day asked for); `status` is "ok", "interpolated", "no_pass", or "error".
 *  "ok" - exact same-day scene; full stats + single-scene provenance present.
 *  "interpolated" - no same-day scene; stats averaged from the two nearest passes
 *    (`before_pass_date` + `after_pass_date` carry the source dates).
 *  "no_pass" - no scene found within the search window on either side.
 *  "error" - this one scene's read failed (e.g. a cold-archived granule); the rest of the series
 *    still resolved. `detail` carries the failure message. Excluded from charts and the push. */
export interface AOISeriesPass {
  status: "ok" | "interpolated" | "no_pass" | "error" | string;
  requested_date?: string;
  index?: string;
  detail?: string;
  pass_date?: string;
  before_pass_date?: string;
  after_pass_date?: string;
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
  before?: AOISeriesPass | null;
  after?: AOISeriesPass | null;
}

export interface AOISeriesResult {
  status: string;
  index: string;
  mode: AOISeriesMode;
  requested: number;
  resolved: number;
  passes: AOISeriesPass[];
}

/** Result of an all-indices series job (ADR 0011 Phase 2): one `AOISeriesResult` per index, keyed
 *  by index name. The single-index job returns a flat `AOISeriesResult` instead; `AOIJob.result`
 *  carries the common single shape, and the studio widens/discriminates when it polls a job it
 *  started with `indices`. */
export interface MultiIndexResult {
  status: string;
  mode: AOISeriesMode;
  indices: Record<string, AOISeriesResult>;
}

/** Status of a multi-pass AOI preview job (GET /analyse/aoi/jobs/{id}). `state` walks
 *  queued -> running (with a {done,total} progress meter) -> done (with `result`) or error. */
export interface AOIJob {
  job_id: string;
  state: "queued" | "running" | "done" | "error" | string;
  progress?: { done: number | null; total: number | null } | null;
  result?: AOISeriesResult | null;
  error?: string | null;
}

/** Response of POST /analyse/aoi/series: the job was accepted onto the queue. Poll `aoiJob`. */
export interface AOIJobEnqueued {
  job_id: string;
  state: string;
}

export interface AOIPushRequest {
  canonical_farm_id: string;
}

export interface AOIPushResult {
  pushed_passes: number;
  status: string;
  ok: boolean;
  dry_run: boolean;
  idempotency_key: string;
  detail: string | null;
}

/** Request to POST /analyse/farm/{id}/series: run the studio engine over the farm's union
 *  geometry. Provide either a single `index` or a non-empty `indices` list (ADR 0011 Phase 2).
 *  Mirrors FarmSeriesRequest in services/api/workspace/analyse.py. */
export interface FarmSeriesRequest {
  index?: string;
  indices?: string[];
  mode: AOISeriesMode;
  dates?: string[];
  months?: number;
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

export interface PipelineHealth {
  fields: number;
  awaiting_backfill: number;
  dead_letters: number;
}

/** One requested date that snapped to a real pass (POST /fields/{id}/collect-dates). `day_gap` is
 *  signed days from the requested date (0 = exact, negative = pass is earlier). */
export interface CollectDatesResolved {
  requested_date: string;
  scene_id: string;
  pass_date: string;
  day_gap: number;
}

/** Result of a targeted field collect: per-date resolution. `enqueued` is the count of new scenes
 *  actually fanned out (resolved dates that snap to an already-stored scene are not recollected).
 *  Mirrors `_plan_collect_dates` in services/worker/tasks/collection.py. */
export interface CollectDatesResult {
  field_id: string;
  requested: number;
  resolved: CollectDatesResolved[];
  skipped: string[];
  enqueued: number;
}

/** A region-boundary layer for the workspace map's overlay toggle (comparison groups, PRD 0002).
 *  `kind` groups layers for the UI: "seeded" is the Natural Region reference, "uploaded"/"drawn"
 *  are analyst-created. Mirrors RegionLayerOut in services/api/workspace/regions.py. */
export interface RegionLayer {
  layer_id: string;
  name: string;
  custodian: string;
  kind: "seeded" | "uploaded" | "drawn" | string;
  region_count: number;
  read_only: boolean;
  year: number | null;
  version: string;
}

/** A layer's boundaries as a GeoJSON FeatureCollection (GET /regions/layers/{id}/boundaries), fed
 *  straight into a MapLibre geojson source. Geometry is WGS84; each feature's properties carry the
 *  boundary's id, name, source, and dominant Natural Region. */
export type RegionFeatureCollection = FeatureCollection;

/** One row of the Ward Watch officer triage queue (GET /ward-watch/triage). Geometry-free; ranked by
 *  movement-label severity then robust deviation. The cohort level, quorum flag and pixel-quality
 *  flag travel on every row for honesty (PRD 0003 §7.1). Mirrors TriageRowOut in
 *  services/api/workspace/ward_watch.py. */
export interface TriageRow {
  rank: number;
  household_id: string;
  village: string | null;
  ward: string;
  dominant_crop: string | null;
  label: string;
  robust_deviation: number;
  cohort_level: string;
  cohort_meets_quorum: boolean;
  low_pixel_quality: boolean;
}

/** One administrative unit's movement-label tally, idiosyncratic and systemic reported separately
 *  (PRD 0003 §10). `distressed` = idiosyncratic + systemic. Mirrors RollupNodeOut. */
export interface RollupNode {
  name: string;
  total: number;
  nominal: number;
  resilient: number;
  idiosyncratic: number;
  systemic: number;
  distressed: number;
  systemic_fraction: number;
}

/** The food-security rollups (GET /ward-watch/rollups): the same households tallied per ward,
 *  district and province, each list worst-first. District/province are a single "unassigned" node
 *  until ward-boundary procurement lands. Mirrors FoodSecurityRollupOut. */
export interface FoodSecurityRollup {
  by_ward: RollupNode[];
  by_district: RollupNode[];
  by_province: RollupNode[];
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
  asOf: (fieldId: string, date: string, index: string, token: string, signal?: AbortSignal) =>
    get<AsOfResolution>(
      `/fields/${fieldId}/as-of?date=${encodeURIComponent(date)}&index=${encodeURIComponent(index)}`,
      token,
      signal,
    ),
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
  analyseAOISeries: (req: AOISeriesRequest, token: string) =>
    send<AOIJobEnqueued>("POST", "/analyse/aoi/series", token, req),
  analyseFarmSeries: (canonicalFarmId: string, req: FarmSeriesRequest, token: string) =>
    send<AOIJobEnqueued>(
      "POST",
      `/analyse/farm/${encodeURIComponent(canonicalFarmId)}/series`,
      token,
      req,
    ),
  aoiJob: (jobId: string, token: string, signal?: AbortSignal) =>
    get<AOIJob>(`/analyse/aoi/jobs/${encodeURIComponent(jobId)}`, token, signal),
  pushAOIResults: (jobId: string, req: AOIPushRequest, token: string) =>
    send<AOIPushResult>(
      "POST",
      `/analyse/aoi/jobs/${encodeURIComponent(jobId)}/push`,
      token,
      req,
    ),
  pipelineHealth: (token: string, signal?: AbortSignal) =>
    get<PipelineHealth>("/pipeline/health", token, signal),
  collectField: (fieldId: string, token: string) =>
    send<{ status: string; field_id: string; by: string }>(
      "POST",
      `/fields/${fieldId}/collect`,
      token,
    ),
  collectDates: (fieldId: string, dates: string[], token: string) =>
    send<CollectDatesResult>("POST", `/fields/${fieldId}/collect-dates`, token, { dates }),
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
  regionLayers: (token: string, signal?: AbortSignal) =>
    get<RegionLayer[]>("/regions/layers", token, signal),
  regionBoundaries: (layerId: string, token: string, signal?: AbortSignal) =>
    get<RegionFeatureCollection>(
      `/regions/layers/${encodeURIComponent(layerId)}/boundaries`,
      token,
      signal,
    ),
  wardWatchTriage: (
    params: { ward?: string; cap?: number },
    token: string,
    signal?: AbortSignal,
  ) => {
    const q = new URLSearchParams();
    if (params.ward) q.set("ward", params.ward);
    if (params.cap) q.set("cap", String(params.cap));
    const qs = q.toString();
    return get<TriageRow[]>(`/ward-watch/triage${qs ? `?${qs}` : ""}`, token, signal);
  },
  wardWatchRollups: (token: string, signal?: AbortSignal) =>
    get<FoodSecurityRollup>("/ward-watch/rollups", token, signal),
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
