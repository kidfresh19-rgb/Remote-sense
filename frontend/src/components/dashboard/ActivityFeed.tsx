import { Article, ArrowRight } from "@phosphor-icons/react";
import { Link } from "@tanstack/react-router";

import { Badge, Skeleton } from "@/components/ui";
import { formatDateShort } from "@/lib/format";
import { useReviewQueue } from "@/lib/queries";

const CONFIDENCE_TONE = {
  high: "positive",
  medium: "neutral",
  low: "caution",
} as const;

const STATUS_TONE = {
  healthy: "positive",
  moderate: "neutral",
  stressed: "caution",
  critical: "critical",
} as const;

function toneLookup<T extends Record<string, string>>(map: T, key: string): T[keyof T] {
  return (key in map ? map[key as keyof T] : "neutral") as T[keyof T];
}

export function ActivityFeed() {
  const queue = useReviewQueue(false, true);
  // Newest passes first; cap at 14 items for the panel height.
  const items = queue.data ? [...queue.data].reverse().slice(0, 14) : [];

  return (
    <div className="flex h-full flex-col">
      <div className="flex shrink-0 items-center gap-2 border-b border-border px-4 py-3">
        <Article size={15} className="shrink-0 text-muted" />
        <h3 className="text-sm font-semibold text-fg">Recent interpretations</h3>
        {queue.data ? (
          <span className="ml-auto text-xs text-muted">{queue.data.length} total</span>
        ) : null}
      </div>

      <div className="min-h-0 flex-1 overflow-y-auto">
        {queue.isPending ? (
          <ul className="divide-y divide-border">
            {Array.from({ length: 7 }).map((_, i) => (
              <li key={i} className="flex flex-col gap-2 px-4 py-3">
                <Skeleton className="h-3.5 w-3/4" />
                <Skeleton className="h-3 w-1/2" />
              </li>
            ))}
          </ul>
        ) : items.length === 0 ? (
          <div className="flex h-full flex-col items-center justify-center gap-2 p-6 text-center">
            <Article size={32} className="text-muted/40" />
            <p className="text-sm text-muted">No interpretations yet</p>
            <p className="text-xs text-muted/60">
              Agronomic reads will appear here once the pipeline runs.
            </p>
          </div>
        ) : (
          <ul className="divide-y divide-border">
            {items.map((item) => (
              <li
                key={item.id}
                className="flex flex-col gap-2 px-4 py-3 transition-colors duration-100 hover:bg-panel-2"
              >
                <div className="flex items-start justify-between gap-2">
                  <div className="min-w-0">
                    <p className="truncate text-xs font-medium text-fg">
                      {item.field_name ?? item.canonical_field_id ?? item.field_id}
                    </p>
                    <p className="truncate text-xs text-muted">
                      {item.crop ? `${item.crop} · ` : ""}
                      {item.canonical_farm_id}
                    </p>
                  </div>
                  <time className="tnum shrink-0 text-xs text-muted">
                    {formatDateShort(item.pass_date)}
                  </time>
                </div>
                <div className="flex flex-wrap items-center gap-1.5">
                  <Badge
                    tone={toneLookup(STATUS_TONE, item.status)}
                    className="text-[10px] leading-none"
                  >
                    {item.status}
                  </Badge>
                  <Badge
                    tone={toneLookup(CONFIDENCE_TONE, item.confidence)}
                    className="text-[10px] leading-none"
                  >
                    {item.confidence}
                  </Badge>
                  {item.needs_review ? (
                    <Badge tone="accent" className="text-[10px] leading-none">
                      needs review
                    </Badge>
                  ) : null}
                </div>
              </li>
            ))}
          </ul>
        )}
      </div>

      <div className="shrink-0 border-t border-border px-4 py-3">
        <Link to="/workspace">
          <button className="flex w-full items-center justify-center gap-1.5 rounded-md py-1.5 text-xs font-medium text-accent transition-opacity duration-150 hover:opacity-70">
            Open workspace
            <ArrowRight size={12} weight="bold" />
          </button>
        </Link>
      </div>
    </div>
  );
}
