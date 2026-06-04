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
