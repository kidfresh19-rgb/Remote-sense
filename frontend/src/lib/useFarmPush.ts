import { useEffect, useState } from "react";

import { usePublishFarm, usePublishStatus } from "./queries";

export type FarmPushPhase =
  | "idle"
  | "enqueuing"
  | "polling"
  | "published"
  | "dead_letter"
  | "enqueue_error";

interface FarmPushState {
  phase: FarmPushPhase;
  resultCount?: number;
  error?: string;
  // True when the gateway recorded the push without sending (recording / dry-run sink), so a
  // dry-run is never shown as a real delivery.
  dryRun?: boolean;
}

export interface FarmPush extends FarmPushState {
  push: () => void;
}

/** One farm's gateway-push state machine: enqueue `POST /farms/{id}/publish`, poll its outbox
 *  status, and settle to published / dead_letter. Shared by the header push modal and the dashboard
 *  farm cards so the two can never drift. `usePublishFarm` invalidates the cached status on enqueue,
 *  so a retry after a dead_letter re-polls fresh rather than re-settling on the stale row. */
export function useFarmPush(canonicalFarmId: string): FarmPush {
  const [view, setView] = useState<FarmPushState>({ phase: "idle" });
  const publish = usePublishFarm();
  const status = usePublishStatus(canonicalFarmId, view.phase === "polling");

  useEffect(() => {
    if (view.phase !== "polling") return;
    const settled = status.data?.status;
    if (settled === "published") {
      setView({
        phase: "published",
        resultCount: status.data?.result_count ?? 0,
        dryRun: status.data?.dry_run ?? false,
      });
    } else if (settled === "dead_letter") {
      setView({ phase: "dead_letter", error: status.data?.last_error ?? "Push failed" });
    }
  }, [status.data, view.phase]);

  const push = () => {
    setView({ phase: "enqueuing" });
    publish.mutate(canonicalFarmId, {
      onSuccess: (data) => setView({ phase: "polling", dryRun: data.dry_run }),
      onError: (err) =>
        setView({
          phase: "enqueue_error",
          error: err instanceof Error ? err.message : String(err),
        }),
    });
  };

  return { ...view, push };
}
