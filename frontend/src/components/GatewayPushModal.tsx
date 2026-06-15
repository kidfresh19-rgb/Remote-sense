import { X, CloudArrowUp, Warning, Heart } from "@phosphor-icons/react";

import type { Farm } from "@/lib/api";
import { healthTone } from "@/lib/health";
import { useFarms } from "@/lib/queries";
import { useFarmPush } from "@/lib/useFarmPush";

import { FarmPushButton } from "./FarmPushButton";
import { EmptyState, ErrorState, LoadingRows } from "./states";
import { Badge } from "./ui";

interface GatewayPushModalProps {
  onClose: () => void;
}

export function GatewayPushModal({ onClose }: GatewayPushModalProps) {
  const query = useFarms();
  const farms = query.data ?? [];

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
              <p className="mb-4 text-xs leading-relaxed text-muted">
                Trigger a manual synchronisation for a farm. This compiles all stored analysis
                indices and published interpretations and pushes them to the gateway.
              </p>
              <ul className="divide-y divide-border overflow-hidden rounded-lg border border-border bg-panel-2">
                {farms.map((farm) => (
                  <FarmPushRow key={farm.canonical_farm_id} farm={farm} />
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

function FarmPushRow({ farm }: { farm: Farm }) {
  const push = useFarmPush(farm.canonical_farm_id);
  const showError =
    (push.phase === "dead_letter" || push.phase === "enqueue_error") && push.error;

  return (
    <li className="flex items-center justify-between gap-4 p-3 text-xs leading-relaxed">
      <div className="min-w-0 flex-1">
        <div className="flex items-center gap-2">
          <span className="truncate font-semibold text-fg">
            {farm.name || farm.canonical_farm_id}
          </span>
          {farm.overall_health ? (
            <Badge tone={healthTone(farm.overall_health)}>
              <Heart size={10} weight="fill" />
              {farm.overall_health}
            </Badge>
          ) : null}
        </div>
        <div className="mt-0.5 truncate text-[10px] text-muted">
          {farm.region ? `${farm.region} · ` : ""}
          {farm.canonical_farm_id}
          {farm.total_fields != null ? ` · ${farm.total_fields} fields` : ""}
          {farm.latest_pass_date ? ` · last pass ${farm.latest_pass_date}` : ""}
        </div>
        {showError ? (
          <div className="mt-1 flex items-center gap-1 truncate text-[10px] text-critical">
            <Warning size={12} />
            <span>{push.error}</span>
          </div>
        ) : null}
      </div>

      <div className="shrink-0">
        <FarmPushButton push={push} className="h-7 gap-1.5 px-2.5 text-[11px]" />
      </div>
    </li>
  );
}
