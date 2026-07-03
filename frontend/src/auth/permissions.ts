import { useToken } from "./TokenProvider";

// Roles that hold the `publish` permission, mirroring rs_core/rbac.py (publisher + admin). This is
// a COSMETIC gate only: it decides whether to render the review actions so a viewer is not shown
// dead buttons. The server is authoritative - every review call is gated by require(PUBLISH), so a
// 403 still surfaces inline if a client renders the action anyway.
// ⚑ CONFIRM: real per-user gating arrives with the gateway OIDC flow (see TokenProvider). Until
// then the role claim is read straight from the bearer token the dev/token-gate supplies.
const PUBLISH_ROLES = new Set(["publisher", "admin"]);

function decodeRoles(token: string | null): string[] {
  if (!token) return [];
  const parts = token.split(".");
  if (parts.length !== 3) return [];
  try {
    const b64 = parts[1].replace(/-/g, "+").replace(/_/g, "/");
    const padded = b64 + "=".repeat((4 - (b64.length % 4)) % 4);
    const claims = JSON.parse(atob(padded)) as { roles?: unknown };
    return Array.isArray(claims.roles)
      ? claims.roles.filter((r): r is string => typeof r === "string")
      : [];
  } catch {
    return [];
  }
}

export function useCanPublish(): boolean {
  const { token } = useToken();
  return decodeRoles(token).some((role) => PUBLISH_ROLES.has(role));
}

// Roles that hold the `run_analysis` permission, mirroring rs_core/rbac.py (analyst + publisher +
// admin). COSMETIC only, same contract as PUBLISH_ROLES above: it decides whether to enable the
// AOI Studio run/backfill action so a viewer is not handed a button that 403s. The server stays
// authoritative - every /analyse call is gated by require(RUN_ANALYSIS).
const RUN_ANALYSIS_ROLES = new Set(["analyst", "publisher", "admin"]);

export function useCanRunAnalysis(): boolean {
  const { token } = useToken();
  return decodeRoles(token).some((role) => RUN_ANALYSIS_ROLES.has(role));
}

// Ward Watch role gates, mirroring the settled rs_core/rbac.py mapping (backlog 0041). COSMETIC
// only - the server gates every endpoint with require(...), so these just decide what to render.
// VIEW_TRIAGE_QUEUE = ward_officer + district_agronomist + admin (the queue + visit cockpit).
const TRIAGE_ROLES = new Set(["ward_officer", "district_agronomist", "admin"]);
// VIEW_FOOD_SECURITY_ROLLUP = district_agronomist + ministry + admin (the rollups + diagnosis export).
const ROLLUP_ROLES = new Set(["district_agronomist", "ministry", "admin"]);
// RECORD_DIAGNOSIS = ward_officer + admin (the field-diagnosis capture form).
const DIAGNOSE_ROLES = new Set(["ward_officer", "admin"]);

export function useCanViewTriage(): boolean {
  const { token } = useToken();
  return decodeRoles(token).some((role) => TRIAGE_ROLES.has(role));
}

export function useCanViewRollups(): boolean {
  const { token } = useToken();
  return decodeRoles(token).some((role) => ROLLUP_ROLES.has(role));
}

export function useCanRecordDiagnosis(): boolean {
  const { token } = useToken();
  return decodeRoles(token).some((role) => DIAGNOSE_ROLES.has(role));
}

/** Whether to surface the Ward Watch nav + route at all: anyone who can see the queue or the
 *  rollups (officer, district, ministry, admin). */
export function useCanAccessWardWatch(): boolean {
  const { token } = useToken();
  return decodeRoles(token).some((role) => TRIAGE_ROLES.has(role) || ROLLUP_ROLES.has(role));
}
