import type { Interpretation } from "@/lib/api";
import { formatDate } from "@/lib/format";
import { useInterpretations } from "@/lib/queries";

import { EmptyState, ErrorState, LoadingRows } from "./states";
import { Badge } from "./ui";

type Tone = "positive" | "caution" | "critical" | "neutral";

// ⚑ CONFIRM: map the agronomy status vocabulary (rs_interpret) to tones once thresholds are locked.
const STATUS_TONE: Record<string, Tone> = {
  healthy: "positive",
  vigorous: "positive",
  stable: "positive",
  stressed: "caution",
  declining: "caution",
  marginal: "caution",
  critical: "critical",
  failing: "critical",
};

export function InterpretationPanel({ fieldId }: { fieldId: string }) {
  const query = useInterpretations(fieldId);

  if (query.isLoading) return <LoadingRows />;
  if (query.isError) return <ErrorState error={query.error} onRetry={() => query.refetch()} />;
  const reads = query.data ?? [];
  if (!reads.length) {
    return (
      <EmptyState
        title="No agronomic reads"
        hint="Reads appear here once drafted. They stay unpublished until an agronomist reviews them."
      />
    );
  }

  return (
    <ul className="divide-y divide-border">
      {[...reads].reverse().map((read) => (
        <InterpretationCard key={read.pass_date} read={read} />
      ))}
    </ul>
  );
}

function InterpretationCard({ read }: { read: Interpretation }) {
  const tone = STATUS_TONE[read.status.toLowerCase()] ?? "neutral";
  return (
    <li className="space-y-2 p-3">
      <div className="flex flex-wrap items-center gap-1.5">
        <span className="mr-1 text-xs text-muted">{formatDate(read.pass_date)}</span>
        <Badge tone={tone}>{read.status}</Badge>
        <Badge tone="neutral">{read.confidence} confidence</Badge>
        {read.needs_review ? <Badge tone="caution">needs review</Badge> : null}
        {read.published ? <Badge tone="accent">published</Badge> : null}
      </div>
      <p className="text-sm leading-relaxed text-fg">{read.narrative}</p>
    </li>
  );
}
