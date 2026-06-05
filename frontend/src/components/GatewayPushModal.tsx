import {
  X,
  CloudArrowUp,
  CheckCircle,
  Warning,
  SpinnerGap,
  ArrowClockwise,
  Heart,
} from "@phosphor-icons/react";
import { useState } from "react";

import { useFarms, usePublishFarm, usePublishStatus } from "@/lib/queries";
import type { Farm } from "@/lib/api";
import { EmptyState, ErrorState, LoadingRows } from "./states";
import { Badge, Button } from "./ui";

interface GatewayPushModalProps {
  onClose: () => void;
}

type PushState = "idle" | "enqueuing" | "polling" | "published" | "dead_letter" | "enqueue_error";

interface FarmPushState {
  state: PushState;
  resultCount?: number;
  error?: string;
}

const healthTone = (health: string | null) => {
  switch (health) {
    case "healthy":
      return "positive" as const;
    case "moderate":
      return "caution" as const;
    case "stressed":
    case "critical":
      return "critical" as const;
    default:
      return "neutral" as const;
  }
};

export function GatewayPushModal({ onClose }: GatewayPushModalProps) {
  const query = useFarms();
  const farms = query.data ?? [];

  const [statuses, setStatuses] = useState<Record<string, FarmPushState>>({});

  return (
    <div className="fixed inset-0 z-50 flex items-start justify-center bg-bg/60 p-4 backdrop-blur-sm sm:p-8">
      <div className="flex max-h-full w-full max-w-md flex-col overflow-hidden rounded-xl border border-border bg-panel shadow-xl">
        <div className="flex items-center justify-between gap-3 border-b border-border px-4 py-3">
          <div className="flex items-center gap-2 text-sm font-semibold text-fg">
            <CloudArrowUp size={18} className="text-accent" />
            <span>Push Data to Gateway</span>
          </div>
          <button
            onClick={onClose}
            aria-label="Close push modal"
            className="rounded-md p-1 text-muted transition-colors hover:bg-panel-2 hover:text-fg"
          >
            <X size={16} />
          </button>
        </div>

        <div className="min-h-0 flex-1 overflow-y-auto p-4">
          {query.isLoading ? (
            <LoadingRows rows={3} />
          ) : query.isError ? (
            <ErrorState error={query.error} onRetry={() => void query.refetch()} />
          ) : farms.length ? (
            <div className="space-y-3">
              <p className="text-xs text-muted leading-relaxed mb-4">
                Trigger a manual synchronisation for a farm. This compiles all stored analysis
                indices and published interpretations and pushes them to the gateway.
              </p>
              <ul className="divide-y divide-border border border-border rounded-lg overflow-hidden bg-panel-2">
                {farms.map((farm) => (
                  <FarmPushRow
                    key={farm.canonical_farm_id}
                    farm={farm}
                    pushState={statuses[farm.canonical_farm_id] || { state: "idle" }}
                    onStateChange={(s) =>
                      setStatuses((prev) => ({ ...prev, [farm.canonical_farm_id]: s }))
                    }
                  />
                ))}
              </ul>
            </div>
          ) : (
            <EmptyState
              title="No farms found"
              hint="You need at least one registered farm to push data to the gateway."
            />
          )}
        </div>
      </div>
    </div>
  );
}

function FarmPushRow({
  farm,
  pushState,
  onStateChange,
}: {
  farm: Farm;
  pushState: FarmPushState;
  onStateChange: (s: FarmPushState) => void;
}) {
  const publishMutation = usePublishFarm();

  // Poll status only when we're in the polling state
  const statusQuery = usePublishStatus(
    farm.canonical_farm_id,
    pushState.state === "polling",
  );

  // React to status query data settling
  const resolvedStatus = statusQuery.data?.status;
  if (
    pushState.state === "polling" &&
    resolvedStatus &&
    (resolvedStatus === "published" || resolvedStatus === "dead_letter")
  ) {
    // Settle the state on next tick to avoid updating during render
    const count = statusQuery.data?.result_count ?? 0;
    const error = statusQuery.data?.last_error ?? undefined;
    queueMicrotask(() => {
      if (resolvedStatus === "published") {
        onStateChange({ state: "published", resultCount: count });
      } else {
        onStateChange({ state: "dead_letter", error: error || "Push failed" });
      }
    });
  }

  const handlePush = () => {
    onStateChange({ state: "enqueuing" });
    publishMutation.mutate(farm.canonical_farm_id, {
      onSuccess: () => {
        onStateChange({ state: "polling" });
      },
      onError: (err) => {
        onStateChange({
          state: "enqueue_error",
          error: err instanceof Error ? err.message : String(err),
        });
      },
    });
  };

  return (
    <li className="flex items-center justify-between gap-4 p-3 text-xs leading-relaxed">
      <div className="min-w-0 flex-1">
        <div className="flex items-center gap-2">
          <span className="font-semibold text-fg truncate">
            {farm.name || farm.canonical_farm_id}
          </span>
          {farm.overall_health ? (
            <Badge tone={healthTone(farm.overall_health)}>
              <Heart size={10} weight="fill" />
              {farm.overall_health}
            </Badge>
          ) : null}
        </div>
        <div className="text-[10px] text-muted truncate mt-0.5">
          {farm.region ? `${farm.region} · ` : ""}
          {farm.canonical_farm_id}
          {farm.total_fields != null ? ` · ${farm.total_fields} fields` : ""}
          {farm.latest_pass_date ? ` · last pass ${farm.latest_pass_date}` : ""}
        </div>

        {/* Error feedback */}
        {(pushState.state === "dead_letter" || pushState.state === "enqueue_error") && (
          <div className="text-[10px] text-critical flex items-center gap-1 mt-1 truncate">
            <Warning size={12} />
            <span>{pushState.error}</span>
          </div>
        )}
      </div>

      <div className="shrink-0">
        {pushState.state === "published" ? (
          <span className="flex items-center gap-1 font-medium text-positive">
            <CheckCircle size={14} weight="fill" />
            <span>Sent{pushState.resultCount ? ` · ${pushState.resultCount}` : ""}</span>
          </span>
        ) : pushState.state === "enqueuing" || pushState.state === "polling" ? (
          <span className="flex items-center gap-1 font-medium text-accent">
            <SpinnerGap size={14} className="animate-spin" />
            <span>{pushState.state === "enqueuing" ? "Queuing…" : "Sending…"}</span>
          </span>
        ) : pushState.state === "dead_letter" || pushState.state === "enqueue_error" ? (
          <Button
            variant="outline"
            className="h-7 px-2.5 text-[11px]"
            onClick={handlePush}
          >
            <ArrowClockwise size={12} />
            Retry
          </Button>
        ) : (
          <Button
            variant="primary"
            className="h-7 px-2.5 text-[11px]"
            onClick={handlePush}
          >
            Push
          </Button>
        )}
      </div>
    </li>
  );
}
