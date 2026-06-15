import { ChartPie, CheckCircle, CloudArrowUp } from "@phosphor-icons/react";
import type { ReactNode } from "react";

import { Skeleton } from "@/components/ui";
import { cn } from "@/lib/format";
import { useFarms, usePipelineHealth } from "@/lib/queries";

const HEALTH_ORDER = ["healthy", "moderate", "stressed", "critical"] as const;
type HealthKey = (typeof HEALTH_ORDER)[number];

const HEALTH_BG: Record<HealthKey, string> = {
  healthy: "bg-positive",
  moderate: "bg-caution",
  stressed: "bg-orange-400",
  critical: "bg-critical",
};
const HEALTH_DOT: Record<HealthKey, string> = {
  healthy: "bg-positive",
  moderate: "bg-caution",
  stressed: "bg-orange-400",
  critical: "bg-critical",
};
const HEALTH_LABEL: Record<HealthKey, string> = {
  healthy: "Healthy",
  moderate: "Moderate",
  stressed: "Stressed",
  critical: "Critical",
};

function Card({
  title,
  icon,
  children,
  loading,
}: {
  title: string;
  icon: ReactNode;
  children: ReactNode;
  loading?: boolean;
}) {
  return (
    <div className="stat-card flex flex-col gap-4 p-5">
      <div className="flex items-center gap-2">
        <span className="flex size-8 shrink-0 items-center justify-center rounded-lg bg-accent/10 text-accent">
          {icon}
        </span>
        <h3 className="text-sm font-semibold text-fg">{title}</h3>
      </div>
      {loading ? (
        <div className="flex flex-col gap-2">
          <Skeleton className="h-4 w-full" />
          <Skeleton className="h-4 w-3/4" />
        </div>
      ) : (
        children
      )}
    </div>
  );
}

function HealthDistributionCard() {
  const farms = useFarms();
  const data = farms.data ?? [];
  const total = data.length;

  const counts: Record<HealthKey, number> = { healthy: 0, moderate: 0, stressed: 0, critical: 0 };
  for (const farm of data) {
    const key = farm.overall_health as HealthKey | null;
    if (key && key in counts) counts[key]++;
  }

  return (
    <Card title="Farm health" icon={<ChartPie size={16} />} loading={farms.isPending}>
      {total === 0 ? (
        <p className="text-xs text-muted">No farms onboarded yet.</p>
      ) : (
        <div className="flex flex-col gap-3">
          <div className="flex h-3 w-full overflow-hidden rounded-full bg-panel-2">
            {HEALTH_ORDER.map((h) => {
              const pct = (counts[h] / total) * 100;
              if (pct === 0) return null;
              return (
                <div
                  key={h}
                  className={cn("h-full transition-all duration-500", HEALTH_BG[h])}
                  style={{ width: `${pct}%` }}
                  title={`${HEALTH_LABEL[h]}: ${counts[h]}`}
                />
              );
            })}
          </div>
          <div className="flex flex-wrap gap-x-4 gap-y-2">
            {HEALTH_ORDER.map((h) => (
              <div key={h} className="flex items-center gap-1.5">
                <span className={cn("size-2 rounded-full", HEALTH_DOT[h])} />
                <span className="text-xs text-muted">{HEALTH_LABEL[h]}</span>
                <span className="tnum text-xs font-semibold text-fg">{counts[h]}</span>
              </div>
            ))}
          </div>
        </div>
      )}
    </Card>
  );
}

function CoverageCard() {
  const health = usePipelineHealth();
  const total = health.data?.fields ?? 0;
  const awaiting = health.data?.awaiting_backfill ?? 0;
  const covered = total - awaiting;
  const pct = total > 0 ? Math.round((covered / total) * 100) : 0;

  return (
    <Card
      title="Collection coverage"
      icon={<CheckCircle size={16} />}
      loading={health.isPending}
    >
      {total === 0 ? (
        <p className="text-xs text-muted">No fields registered yet.</p>
      ) : (
        <div className="flex flex-col gap-3">
          <div className="flex items-end justify-between">
            <p className="tnum text-4xl font-bold leading-none tracking-tight text-fg">{pct}%</p>
            <p className="text-xs text-muted">
              {covered} of {total} fields
            </p>
          </div>
          <div className="h-2 w-full overflow-hidden rounded-full bg-panel-2">
            <div
              className="h-full rounded-full bg-accent transition-all duration-700"
              style={{ width: `${pct}%` }}
            />
          </div>
          {awaiting > 0 ? (
            <p className="text-xs text-caution">
              {awaiting} field{awaiting === 1 ? "" : "s"} awaiting first backfill
            </p>
          ) : (
            <p className="text-xs text-positive">All fields have satellite data</p>
          )}
        </div>
      )}
    </Card>
  );
}

function GatewayCard() {
  const health = usePipelineHealth();
  const deadLetters = health.data?.dead_letters ?? 0;
  const isOk = deadLetters === 0;

  return (
    <Card title="Gateway sync" icon={<CloudArrowUp size={16} />} loading={health.isPending}>
      <div className="flex flex-col gap-3">
        <div className="flex items-end justify-between">
          <p
            className={cn(
              "tnum text-4xl font-bold leading-none tracking-tight",
              isOk ? "text-positive" : "text-critical",
            )}
          >
            {deadLetters}
          </p>
          <p className="text-xs text-muted">failed pushes</p>
        </div>
        <p className={cn("text-xs", isOk ? "text-positive" : "text-critical")}>
          {isOk
            ? "All gateway pushes delivered"
            : `${deadLetters} push${deadLetters === 1 ? "" : "es"} in dead-letter queue`}
        </p>
      </div>
    </Card>
  );
}

export function AnalyticsCards() {
  return (
    <div className="grid grid-cols-1 gap-4 sm:grid-cols-3">
      <HealthDistributionCard />
      <CoverageCard />
      <GatewayCard />
    </div>
  );
}
