import { useQuery } from "@tanstack/react-query";

import { useToken } from "@/auth/TokenProvider";

import { api } from "./api";
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

export function useTimeseries(fieldId: string | null, index: IndexKey) {
  const { token } = useToken();
  return useQuery({
    queryKey: ["timeseries", fieldId, index],
    queryFn: ({ signal }) => api.timeseries(fieldId!, index, token!, signal),
    enabled: !!token && !!fieldId,
  });
}

export function useScenes(fieldId: string | null) {
  const { token } = useToken();
  return useQuery({
    queryKey: ["scenes", fieldId],
    queryFn: ({ signal }) => api.scenes(fieldId!, token!, signal),
    enabled: !!token && !!fieldId,
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

export function useAudit(fieldId: string | null) {
  const { token } = useToken();
  return useQuery({
    queryKey: ["audit", fieldId],
    queryFn: ({ signal }) => api.audit(fieldId!, token!, signal),
    enabled: !!token && !!fieldId,
  });
}
