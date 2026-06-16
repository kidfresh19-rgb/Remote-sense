import { ArrowRight, ChartLineUp, Heart, Plant, Warning } from "@phosphor-icons/react";
import { Link } from "@tanstack/react-router";

import { useCanPublish } from "@/auth/permissions";
import { FarmPushButton } from "@/components/FarmPushButton";
import { EmptyState, ErrorState, LoadingRows } from "@/components/states";
import { Badge, buttonClasses } from "@/components/ui";
import type { Farm } from "@/lib/api";
import { healthTone } from "@/lib/health";
import { useFarms } from "@/lib/queries";
import { useFarmPush } from "@/lib/useFarmPush";

/** Per-farm cards with an inline gateway-push action plus an "Analyse dates" launcher. The push
 *  reuses the existing `POST /farms/{id}/publish` path (geometry-free results only, invariant 6)
 *  and is shown only to users who hold `publish`; the server is authoritative regardless. The
 *  launcher deep-links to the AOI Studio with this farm pre-selected and is a read-only analyse
 *  action, so it is shown to every signed-in user. */
export function FarmCards() {
  const query = useFarms();
  const farms = query.data ?? [];
  const canPublish = useCanPublish();

  if (query.isLoading) return <LoadingRows rows={3} />;
  if (query.isError) return <ErrorState error={query.error} onRetry={() => void query.refetch()} />;
  if (!farms.length) {
    return (
      <EmptyState
        title="No farms yet"
        hint="Farms onboard through the gateway. Once one is registered it appears here."
      />
    );
  }

  return (
    <div className="grid grid-cols-1 gap-4 sm:grid-cols-2 lg:grid-cols-3">
      {farms.map((farm) => (
        <FarmCard key={farm.canonical_farm_id} farm={farm} canPublish={canPublish} />
      ))}
    </div>
  );
}

function FarmCard({ farm, canPublish }: { farm: Farm; canPublish: boolean }) {
  const push = useFarmPush(farm.canonical_farm_id);
  const showError =
    (push.phase === "dead_letter" || push.phase === "enqueue_error") && push.error;

  return (
    <div className="stat-card flex flex-col gap-3 p-5">
      <div className="flex items-start justify-between gap-2">
        <div className="min-w-0">
          <p className="truncate text-sm font-semibold text-fg">
            {farm.name || farm.canonical_farm_id}
          </p>
          <p className="truncate text-xs text-muted">{farm.region || farm.canonical_farm_id}</p>
        </div>
        {farm.overall_health ? (
          <Badge tone={healthTone(farm.overall_health)} className="shrink-0">
            <Heart size={10} weight="fill" />
            {farm.overall_health}
          </Badge>
        ) : null}
      </div>

      <div className="flex flex-wrap gap-x-4 gap-y-1 text-xs text-muted">
        <span className="inline-flex items-center gap-1">
          <Plant size={12} />
          <span className="tabular-nums text-fg">{farm.total_fields ?? "—"}</span> fields
        </span>
        <span>
          last pass{" "}
          <span className="tabular-nums text-fg">{farm.latest_pass_date ?? "—"}</span>
        </span>
      </div>

      <div className="mt-1 flex min-h-8 flex-wrap items-center gap-2">
        {canPublish ? (
          <FarmPushButton push={push} className="h-8 gap-1.5 px-2.5 text-xs" />
        ) : null}
        <Link
          to="/aoi-studio"
          search={{ farm: farm.canonical_farm_id }}
          aria-label={`Analyse custom dates for ${farm.name || farm.canonical_farm_id}`}
          className={buttonClasses("outline", "h-8 gap-1.5 px-2.5 text-xs")}
        >
          <ChartLineUp size={14} />
          Analyse dates
          <ArrowRight size={13} />
        </Link>
      </div>
      {showError ? (
        <p className="flex items-center gap-1 text-[11px] text-critical">
          <Warning size={12} />
          <span className="truncate">{push.error}</span>
        </p>
      ) : null}
    </div>
  );
}
