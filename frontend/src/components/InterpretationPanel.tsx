import { Leaf } from "@phosphor-icons/react";
import { useState } from "react";

import { useCanPublish } from "@/auth/permissions";
import { ApiError, type Interpretation, type ReviewInput } from "@/lib/api";
import { formatDate } from "@/lib/format";
import { useInterpretations, useReviewInterpretation } from "@/lib/queries";

import { EmptyState, ErrorState, LoadingRows } from "./states";
import { Badge, Button } from "./ui";

type Tone = "positive" | "caution" | "critical" | "neutral";

// Map the agronomic status to a badge tone. `status` is the headline band label from rs_interpret
// (service.py _status: the NDVI band, falling back to NDRE/NDMI), so the keys are the band labels
// in rs_interpret/thresholds.py. Anything unmapped (e.g. "unknown") falls through to "neutral".
const STATUS_TONE: Record<string, Tone> = {
  // Vigour bands (NDVI / EVI2 / SAVI)
  bare: "caution",
  sparse: "caution",
  developing: "neutral",
  vigorous: "positive",
  dense: "positive",
  // Red-edge / nitrogen (NDRE)
  low: "caution",
  good: "positive",
  high: "positive",
  // Moisture (NDMI)
  dry: "caution",
  adequate: "positive",
  wet: "positive",
  // Shared mid band (NDRE + NDMI)
  moderate: "neutral",
};

type ReviewMutation = ReturnType<typeof useReviewInterpretation>;

export function InterpretationPanel({ fieldId }: { fieldId: string }) {
  const query = useInterpretations(fieldId);
  const canPublish = useCanPublish();
  const review = useReviewInterpretation(fieldId);

  if (query.isLoading) return <LoadingRows />;
  if (query.isError) return <ErrorState error={query.error} onRetry={() => query.refetch()} />;
  const reads = query.data ?? [];
  if (!reads.length) {
    return (
      <EmptyState
        icon={<Leaf size={32} weight="duotone" className="text-accent animate-pulse" />}
        title="No agronomic reads yet"
        hint={
          canPublish
            ? "Reads are drafted automatically when the analysis pipeline processes a pass. Once a draft appears here, review and publish it to send its narrative to the farmer."
            : "Reads are drafted automatically when the analysis pipeline processes a pass. They stay unpublished until an agronomist reviews them."
        }
      />
    );
  }

  return (
    <ul className="divide-y divide-border">
      {[...reads].reverse().map((read) => (
        <InterpretationCard key={read.id} read={read} canPublish={canPublish} review={review} />
      ))}
    </ul>
  );
}

function InterpretationCard({
  read,
  canPublish,
  review,
}: {
  read: Interpretation;
  canPublish: boolean;
  review: ReviewMutation;
}) {
  const tone = STATUS_TONE[read.status.toLowerCase()] ?? "neutral";
  const [editing, setEditing] = useState(false);
  const [draft, setDraft] = useState(read.narrative);
  const [error, setError] = useState<string | null>(null);
  const pending = review.isPending && review.variables?.id === read.id;

  const submit = (input: ReviewInput) => {
    setError(null);
    review.mutate(
      { id: read.id, input },
      {
        onSuccess: () => setEditing(false),
        onError: (err) =>
          setError(
            err instanceof ApiError && err.isAuth
              ? "You do not have permission to publish reads."
              : err instanceof Error
                ? err.message
                : "Review failed. Try again.",
          ),
      },
    );
  };

  const startEditing = () => {
    setDraft(read.narrative);
    setError(null);
    setEditing(true);
  };
  const cancelEditing = () => {
    setDraft(read.narrative);
    setError(null);
    setEditing(false);
  };

  return (
    <li className="space-y-2 p-3">
      <div className="flex flex-wrap items-center gap-1.5">
        <span className="mr-1 text-xs text-muted">{formatDate(read.pass_date)}</span>
        <Badge tone={tone}>{read.status}</Badge>
        <Badge tone="neutral">{read.confidence} confidence</Badge>
        {read.needs_review ? <Badge tone="caution">needs review</Badge> : null}
        {read.published ? <Badge tone="accent">published</Badge> : null}
      </div>

      {(read.gdd_accumulation !== undefined || read.total_precipitation !== undefined || read.recent_activities?.length) && (
        <div className="grid grid-cols-3 gap-2 py-1.5 px-2.5 bg-panel-2/30 rounded-md border border-border/30 text-xs my-1">
          <div className="flex flex-col">
            <span className="text-[9px] font-bold text-muted uppercase tracking-wider">14d Heat (GDD)</span>
            <span className="font-semibold text-fg">
              {read.gdd_accumulation != null ? `${read.gdd_accumulation.toFixed(1)} °C-day` : "—"}
            </span>
          </div>
          <div className="flex flex-col">
            <span className="text-[9px] font-bold text-muted uppercase tracking-wider">14d Rain</span>
            <span className="font-semibold text-fg">
              {read.total_precipitation != null ? `${read.total_precipitation.toFixed(1)} mm` : "—"}
            </span>
          </div>
          <div className="flex flex-col">
            <span className="text-[9px] font-bold text-muted uppercase tracking-wider">30d Farm Events</span>
            <span className="font-semibold text-fg">
              {read.recent_activities != null && read.recent_activities.length > 0 ? (
                <span className="cursor-help" title={read.recent_activities.map(a => `${a.date}: ${a.activity}`).join("\n")}>
                  {read.recent_activities.length} {read.recent_activities.length === 1 ? "event" : "events"}
                </span>
              ) : (
                "—"
              )}
            </span>
          </div>
        </div>
      )}

      {editing ? (
        <textarea
          value={draft}
          onChange={(e) => setDraft(e.target.value)}
          rows={5}
          disabled={pending}
          aria-label="Edit the agronomic narrative"
          className="w-full resize-y rounded-md border border-border bg-panel px-2 py-1.5 text-sm leading-relaxed text-fg focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-accent disabled:opacity-50"
        />
      ) : (
        <p className="text-sm leading-relaxed text-fg">{read.narrative}</p>
      )}

      {read.reviewed_by ? (
        <p className="text-[11px] text-muted">
          Reviewed by {read.reviewed_by}
          {read.reviewed_at ? ` · ${formatDate(read.reviewed_at)}` : ""}
        </p>
      ) : null}

      {canPublish ? (
        <div className="flex flex-wrap items-center gap-2 pt-0.5">
          {editing ? (
            <>
              <Button
                variant="primary"
                disabled={pending || !draft.trim()}
                onClick={() => submit({ publish: true, narrative: draft.trim() })}
              >
                {pending ? "Saving..." : "Save & publish"}
              </Button>
              <Button
                variant="outline"
                disabled={pending || !draft.trim()}
                onClick={() => submit({ publish: false, narrative: draft.trim() })}
              >
                Save unpublished
              </Button>
              <Button variant="ghost" disabled={pending} onClick={cancelEditing}>
                Cancel
              </Button>
            </>
          ) : (
            <>
              <Button variant="outline" disabled={pending} onClick={startEditing}>
                Edit
              </Button>
              {read.published ? (
                <Button
                  variant="outline"
                  disabled={pending}
                  onClick={() => submit({ publish: false })}
                >
                  {pending ? "Working..." : "Unpublish"}
                </Button>
              ) : (
                <Button
                  variant="primary"
                  disabled={pending}
                  onClick={() => submit({ publish: true })}
                >
                  {pending ? "Publishing..." : "Approve & publish"}
                </Button>
              )}
            </>
          )}
        </div>
      ) : null}

      {error ? <p className="text-xs text-critical">{error}</p> : null}
    </li>
  );
}
