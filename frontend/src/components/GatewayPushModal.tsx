import { X, CloudArrowUp, CheckCircle, Warning } from "@phosphor-icons/react";
import { useState } from "react";

import { useFarms, usePublishFarm } from "@/lib/queries";
import { EmptyState, ErrorState, LoadingRows } from "./states";
import { Button } from "./ui";

interface GatewayPushModalProps {
  onClose: () => void;
}

export function GatewayPushModal({ onClose }: GatewayPushModalProps) {
  const query = useFarms();
  const publishMutation = usePublishFarm();
  const farms = query.data ?? [];

  // Track the push status per canonical_farm_id
  const [statuses, setStatuses] = useState<Record<string, { state: "idle" | "loading" | "success" | "error"; message?: string }>>({});

  const handlePush = (canonicalFarmId: string) => {
    setStatuses((prev) => ({
      ...prev,
      [canonicalFarmId]: { state: "loading" },
    }));

    publishMutation.mutate(canonicalFarmId, {
      onSuccess: () => {
        setStatuses((prev) => ({
          ...prev,
          [canonicalFarmId]: { state: "success" },
        }));
      },
      onError: (err) => {
        setStatuses((prev) => ({
          ...prev,
          [canonicalFarmId]: {
            state: "error",
            message: err instanceof Error ? err.message : String(err),
          },
        }));
      },
    });
  };

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
                Trigger a manual synchronisation for a farm. This compiles all stored analysis indices and published interpretations and enqueues an asynchronous gateway push.
              </p>
              <ul className="divide-y divide-border border border-border rounded-lg overflow-hidden bg-panel-2">
                {farms.map((farm) => {
                  const status = statuses[farm.canonical_farm_id] || { state: "idle" };
                  return (
                    <li
                      key={farm.canonical_farm_id}
                      className="flex items-center justify-between gap-4 p-3 text-xs leading-relaxed"
                    >
                      <div className="min-w-0 flex-1">
                        <div className="font-semibold text-fg truncate">
                          {farm.name || farm.canonical_farm_id}
                        </div>
                        <div className="text-[10px] text-muted truncate">
                          {farm.region ? `${farm.region} · ` : ""}{farm.canonical_farm_id}
                        </div>
                        {status.state === "error" && (
                          <div className="text-[10px] text-critical flex items-center gap-1 mt-1 truncate">
                            <Warning size={12} />
                            <span>{status.message}</span>
                          </div>
                        )}
                      </div>

                      <div className="shrink-0">
                        {status.state === "success" ? (
                          <span className="flex items-center gap-1 font-medium text-accent">
                            <CheckCircle size={14} weight="fill" />
                            <span>Queued</span>
                          </span>
                        ) : (
                          <Button
                            variant={status.state === "error" ? "outline" : "primary"}
                            className="h-7 px-2.5 text-[11px]"
                            disabled={status.state === "loading"}
                            onClick={() => handlePush(farm.canonical_farm_id)}
                          >
                            {status.state === "loading" ? "Pushing..." : "Push"}
                          </Button>
                        )}
                      </div>
                    </li>
                  );
                })}
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
