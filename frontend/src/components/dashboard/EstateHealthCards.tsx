import {
  ArrowsClockwise,
  Buildings,
  ClipboardText,
  GlobeHemisphereWest,
  Plant,
} from "@phosphor-icons/react";
import type { ReactNode } from "react";

import { Skeleton } from "@/components/ui";
import { cn } from "@/lib/format";
import { useFarms, usePipelineHealth, useReviewQueue } from "@/lib/queries";

type Tone = "neutral" | "caution" | "critical";

const toneIcon: Record<Tone, string> = {
  neutral: "bg-accent/10 text-accent",
  caution: "bg-caution/10 text-caution",
  critical: "bg-critical/10 text-critical",
};
const toneValue: Record<Tone, string> = {
  neutral: "text-fg",
  caution: "text-caution",
  critical: "text-critical",
};

interface StatCardProps {
  icon: ReactNode;
  label: string;
  value: string | number;
  tone?: Tone;
  loading?: boolean;
  sub?: string;
}

function StatCard({ icon, label, value, tone = "neutral", loading, sub }: StatCardProps) {
  return (
    <div className="stat-card flex flex-col gap-4 p-5">
      <div className={cn("flex size-10 items-center justify-center rounded-xl", toneIcon[tone])}>
        {icon}
      </div>
      <div>
        {loading ? (
          <Skeleton className="mb-2 h-10 w-20" />
        ) : (
          <p className={cn("tnum text-4xl font-bold leading-none tracking-tight", toneValue[tone])}>
            {value}
          </p>
        )}
        <p className="mt-2 text-sm text-muted">{label}</p>
        {sub && !loading ? <p className="mt-0.5 text-xs text-muted/60">{sub}</p> : null}
      </div>
    </div>
  );
}

export function EstateHealthCards() {
  const farms = useFarms();
  const health = usePipelineHealth();
  const queue = useReviewQueue(true, true);

  const totalFarms = farms.data?.length ?? 0;
  const totalFields = farms.data?.reduce((s, f) => s + (f.total_fields ?? 0), 0) ?? 0;
  const totalAreaRaw = farms.data?.reduce((s, f) => s + (f.total_area_hectares ?? 0), 0) ?? 0;
  const totalArea =
    totalAreaRaw > 0
      ? `${totalAreaRaw.toLocaleString("en-ZW", { maximumFractionDigits: 0 })} ha`
      : "—";
  const awaitingBackfill = health.data?.awaiting_backfill ?? 0;
  const pendingReviews = queue.data?.length ?? 0;

  return (
    <div className="grid grid-cols-2 gap-4 sm:grid-cols-3 lg:grid-cols-5">
      <StatCard
        icon={<Buildings size={20} />}
        label="Farms"
        value={totalFarms}
        loading={farms.isPending}
      />
      <StatCard
        icon={<Plant size={20} />}
        label="Fields"
        value={totalFields}
        loading={farms.isPending}
        sub={totalFarms > 0 ? `across ${totalFarms} farm${totalFarms === 1 ? "" : "s"}` : undefined}
      />
      <StatCard
        icon={<GlobeHemisphereWest size={20} />}
        label="Total area"
        value={totalArea}
        loading={farms.isPending}
      />
      <StatCard
        icon={<ArrowsClockwise size={20} />}
        label="Awaiting backfill"
        value={awaitingBackfill}
        tone={awaitingBackfill > 0 ? "caution" : "neutral"}
        loading={health.isPending}
        sub={awaitingBackfill > 0 ? "fields pending first pass" : undefined}
      />
      <StatCard
        icon={<ClipboardText size={20} />}
        label="Pending reviews"
        value={pendingReviews}
        tone={pendingReviews > 5 ? "caution" : "neutral"}
        loading={queue.isPending}
        sub={pendingReviews > 0 ? "awaiting agronomist sign-off" : undefined}
      />
    </div>
  );
}
