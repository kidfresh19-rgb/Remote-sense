import { useMutation, useQueries, useQuery, useQueryClient } from "@tanstack/react-query";
import type { Geometry } from "geojson";

import { useToken } from "@/auth/TokenProvider";

import {
  api,
  ApiError,
  type AOIPushRequest,
  type AOISeriesRequest,
  type Farm,
  type ReviewInput,
} from "./api";
import type { IndexKey } from "./indices";

export function useFarms() {
  const { token } = useToken();
  return useQuery({
    queryKey: ["farms"],
    queryFn: ({ signal }) => api.farms(token!, signal),
    enabled: !!token,
  });
}

export function useFields(canonicalFarmId: string | null) {
  const { token } = useToken();
  return useQuery({
    queryKey: ["fields", canonicalFarmId],
    queryFn: ({ signal }) => api.fields(canonicalFarmId!, token!, signal),
    enabled: !!token && !!canonicalFarmId,
  });
}

// `collecting` turns on short-interval polling so a freshly triggered backfill surfaces its first
// passes without a manual refresh; polling stops itself once usable data has landed.
export function useTimeseries(fieldId: string | null, index: IndexKey, collecting = false) {
  const { token } = useToken();
  return useQuery({
    queryKey: ["timeseries", fieldId, index],
    queryFn: ({ signal }) => api.timeseries(fieldId!, index, token!, signal),
    enabled: !!token && !!fieldId,
    refetchInterval: (query) =>
      collecting && !query.state.data?.some((p) => p.mean !== null) ? 8000 : false,
  });
}

export function useScenes(fieldId: string | null, collecting = false) {
  const { token } = useToken();
  return useQuery({
    queryKey: ["scenes", fieldId],
    queryFn: ({ signal }) => api.scenes(fieldId!, token!, signal),
    enabled: !!token && !!fieldId,
    refetchInterval: (query) =>
      collecting && !(query.state.data && query.state.data.length > 0) ? 8000 : false,
  });
}

/** Resolve an arbitrary calendar date to the field's nearest usable passes (S3.1). Enabled only
 *  while a date is requested; SceneList's effect applies the resolved pass to the workspace. */
export function useAsOf(fieldId: string | null, date: string | null, index: IndexKey) {
  const { token } = useToken();
  return useQuery({
    queryKey: ["as-of", fieldId, date, index],
    queryFn: ({ signal }) => api.asOf(fieldId!, date!, index, token!, signal),
    enabled: !!token && !!fieldId && !!date,
  });
}

export function useInterpretations(fieldId: string | null) {
  const { token } = useToken();
  return useQuery({
    queryKey: ["interpretations", fieldId],
    queryFn: ({ signal }) => api.interpretations(fieldId!, token!, signal),
    enabled: !!token && !!fieldId,
  });
}

export function useReviewInterpretation(fieldId: string | null) {
  const { token } = useToken();
  const qc = useQueryClient();
  return useMutation({
    mutationFn: ({ id, input }: { id: string; input: ReviewInput }) =>
      api.reviewInterpretation(fieldId!, id, input, token!),
    onSuccess: () => {
      qc.invalidateQueries({ queryKey: ["interpretations", fieldId] });
      qc.invalidateQueries({ queryKey: ["review-queue"] });
    },
  });
}

export function useReviewQueue(needsReview: boolean, enabled: boolean) {
  const { token } = useToken();
  return useQuery({
    queryKey: ["review-queue", needsReview],
    queryFn: ({ signal }) => api.reviewQueue(needsReview, token!, signal),
    enabled: !!token && enabled,
  });
}

/** Review action issued from the cross-field queue, where each row carries its own field id.
 *  Invalidates both the queue and the affected field's interpretations. */
export function useReviewFromQueue() {
  const { token } = useToken();
  const qc = useQueryClient();
  return useMutation({
    mutationFn: ({ fieldId, id, input }: { fieldId: string; id: string; input: ReviewInput }) =>
      api.reviewInterpretation(fieldId, id, input, token!),
    onSuccess: (_data, { fieldId }) => {
      qc.invalidateQueries({ queryKey: ["interpretations", fieldId] });
      qc.invalidateQueries({ queryKey: ["review-queue"] });
    },
  });
}

export function useAudit(fieldId: string | null) {
  const { token } = useToken();
  return useQuery({
    queryKey: ["audit", fieldId],
    queryFn: ({ signal }) => api.audit(fieldId!, token!, signal),
    enabled: !!token && !!fieldId,
  });
}

export function useAnnotations(fieldId: string | null) {
  const { token } = useToken();
  return useQuery({
    queryKey: ["annotations", fieldId],
    queryFn: ({ signal }) => api.annotations(fieldId!, token!, signal),
    enabled: !!token && !!fieldId,
  });
}

export function useAddAnnotation(fieldId: string | null) {
  const { token } = useToken();
  const qc = useQueryClient();
  return useMutation({
    mutationFn: (input: { body: string; pass_date: string | null }) =>
      api.addAnnotation(fieldId!, input, token!),
    onSuccess: () => qc.invalidateQueries({ queryKey: ["annotations", fieldId] }),
  });
}

export function useDeleteAnnotation(fieldId: string | null) {
  const { token } = useToken();
  const qc = useQueryClient();
  return useMutation({
    mutationFn: (annotationId: string) => api.deleteAnnotation(fieldId!, annotationId, token!),
    onSuccess: () => qc.invalidateQueries({ queryKey: ["annotations", fieldId] }),
  });
}

export function usePublishFarm() {
  const { token } = useToken();
  const qc = useQueryClient();
  return useMutation({
    mutationFn: (canonicalFarmId: string) => api.publishFarm(canonicalFarmId, token!),
    onSuccess: (_data, canonicalFarmId) => {
      // Invalidate any cached status so polling picks up the new push
      qc.invalidateQueries({ queryKey: ["publish-status", canonicalFarmId] });
    },
  });
}

/** Poll a farm's latest push status every 3 s while `enabled` is true. Stops polling once the
 *  status settles to `published` or `dead_letter`. Returns undefined (not an error) when no push
 *  has been recorded yet (404). */
export function usePublishStatus(canonicalFarmId: string | null, enabled: boolean) {
  const { token } = useToken();
  return useQuery({
    queryKey: ["publish-status", canonicalFarmId],
    queryFn: async ({ signal }) => {
      try {
        return await api.publishStatus(canonicalFarmId!, token!, signal);
      } catch (err) {
        // 404 means no push recorded yet — return null, not an error
        if (err instanceof ApiError && err.status === 404) return null;
        throw err;
      }
    },
    enabled: !!token && !!canonicalFarmId && enabled,
    refetchInterval: (query) => {
      const status = query.state.data?.status;
      if (status === "published" || status === "dead_letter") return false;
      return 3000;
    },
  });
}

export function useAnalyseAOI() {
  const { token } = useToken();
  return useMutation({
    mutationFn: ({ geometry, index }: { geometry: Geometry; index: string }) =>
      api.analyseAOI(geometry, index, token!),
  });
}

/** Start a multi-pass AOI preview (AOI Studio). Resolves to `{ job_id }`; feed that into
 *  `useAOIJob` to poll for progress and results. */
export function useAnalyseAOISeries() {
  const { token } = useToken();
  return useMutation({
    mutationFn: (req: AOISeriesRequest) => api.analyseAOISeries(req, token!),
  });
}

/** Push the resolved 'ok' passes of a completed AOI Studio job to the gateway under a farm. */
export function usePushAOIResults() {
  const { token } = useToken();
  return useMutation({
    mutationFn: ({ jobId, req }: { jobId: string; req: AOIPushRequest }) =>
      api.pushAOIResults(jobId, req, token!),
  });
}

/** Poll a multi-pass AOI preview job every 1.5 s until it settles to `done` or `error`. Enabled
 *  only while a job id is held; the work runs on the worker, so this is how results surface. */
export function useAOIJob(jobId: string | null) {
  const { token } = useToken();
  return useQuery({
    queryKey: ["aoi-job", jobId],
    queryFn: ({ signal }) => api.aoiJob(jobId!, token!, signal),
    enabled: !!token && !!jobId,
    refetchInterval: (query) => {
      const state = query.state.data?.state;
      return state === "done" || state === "error" ? false : 1500;
    },
  });
}

/** Trigger an on-demand backfill for a field. The work runs on the worker, so callers poll the
 *  field's reads (see `collecting` on useTimeseries/useScenes) to surface results as they land. */
export function usePipelineHealth() {
  const { token } = useToken();
  return useQuery({
    queryKey: ["pipeline-health"],
    queryFn: ({ signal }) => api.pipelineHealth(token!, signal),
    enabled: !!token,
    refetchInterval: 30_000,
  });
}

export function useAllFields(farms: Farm[] | undefined) {
  const { token } = useToken();
  return useQueries({
    queries: (farms ?? []).map((farm) => ({
      queryKey: ["fields", farm.canonical_farm_id] as const,
      queryFn: ({ signal }: { signal: AbortSignal }) =>
        api.fields(farm.canonical_farm_id, token!, signal),
      enabled: !!token && !!farms,
      staleTime: 60_000,
    })),
  });
}

export function useCollectField(fieldId: string | null) {
  const { token } = useToken();
  return useMutation({
    mutationFn: () => api.collectField(fieldId!, token!),
  });
}

/** Targeted "collect specific dates" for a field. Resolves to the per-date plan; on success
 *  invalidate the field's passes/series so the newly collected dates surface as they land. */
export function useCollectDates(fieldId: string | null) {
  const { token } = useToken();
  const qc = useQueryClient();
  return useMutation({
    mutationFn: (dates: string[]) => api.collectDates(fieldId!, dates, token!),
    onSuccess: () => {
      qc.invalidateQueries({ queryKey: ["scenes", fieldId] });
      qc.invalidateQueries({ queryKey: ["timeseries", fieldId] });
    },
  });
}
