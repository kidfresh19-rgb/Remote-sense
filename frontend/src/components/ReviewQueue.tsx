import { ArrowSquareOut, X } from "@phosphor-icons/react";
import { useState } from "react";

import { ApiError, type ReviewQueueItem } from "@/lib/api";
import { formatDate } from "@/lib/format";
import { useReviewFromQueue, useReviewQueue } from "@/lib/queries";
import { useWorkspace } from "@/state/workspace";

import { EmptyState, ErrorState, LoadingRows } from "./states";
import { Badge, Button, SegmentedControl } from "./ui";

type Filter = "needs_review" | "all";

type ReviewMutation = ReturnType<typeof useReviewFromQueue>;

/** Cross-field agronomist review surface (Phase E). A geometry-free list of every drafted read so
 *  reviewing is not field-by-field; publish/withhold inline or open the field for the full panel. */
export function ReviewQueue({ onClose }: { onClose: () => void }) {
  const [filter, setFilter] = useState<Filter>("needs_review");
  const needsReview = filter === "needs_review";
  const query = useReviewQueue(needsReview, true);
  const review = useReviewFromQueue();
  const items = query.data ?? [];

  return (
    <div className="fixed inset-0 z-50 flex items-start justify-center bg-bg/60 p-4 backdrop-blur-sm sm:p-8">
      <div className="flex max-h-full w-full max-w-2xl flex-col overflow-hidden rounded-xl border border-border bg-panel shadow-xl">
        <div className="flex items-center justify-between gap-3 border-b border-border px-4 py-3">
          <div className="flex min-w-0 items-center gap-3">
            <h2 className="text-sm font-semibold text-fg">Review queue</h2>
            <SegmentedControl<Filter>
              ariaLabel="Review filter"
              value={filter}
              onChange={setFilter}
              options={[
                { value: "needs_review", label: "Needs review" },
                { value: "all", label: "All" },
              ]}
            />
          </div>
          <button
            onClick={onClose}
            aria-label="Close review queue"
            className="rounded-md p-1 text-muted transition-colors hover:bg-panel-2 hover:text-fg"
          >
            <X size={16} />
          </button>
        </div>

        <div className="min-h-0 flex-1 overflow-y-auto">
          {query.isLoading ? (
            <LoadingRows />
          ) : query.isError ? (
            <ErrorState error={query.error} onRetry={() => void query.refetch()} />
          ) : items.length ? (
            <ul className="divide-y divide-border">
              {items.map((item) => (
                <QueueRow key={item.id} item={item} review={review} onClose={onClose} />
              ))}
            </ul>
          ) : (
            <EmptyState
              title={needsReview ? "Nothing waiting for review" : "No agronomic reads yet"}
              hint={
                needsReview
                  ? "Drafted reads that need an agronomist appear here across every field."
                  : "Reads appear here once the interpretation worker drafts them."
              }
            />
          )}
        </div>
      </div>
    </div>
  );
}

function QueueRow({
  item,
  review,
  onClose,
}: {
  item: ReviewQueueItem;
  review: ReviewMutation;
  onClose: () => void;
}) {
  const { restoreView, index } = useWorkspace();
  const [error, setError] = useState<string | null>(null);
  const pending = review.isPending && review.variables?.id === item.id;
  const fieldLabel = item.field_name ?? item.canonical_field_id ?? item.field_id;

  const submit = (publish: boolean) => {
    setError(null);
    review.mutate(
      { fieldId: item.field_id, id: item.id, input: { publish } },
      {
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

  const openField = () => {
    restoreView({ farmId: item.canonical_farm_id, fieldId: item.field_id, index });
    onClose();
  };

  return (
    <li className="space-y-2 p-3">
      <div className="flex flex-wrap items-center gap-1.5">
        <span className="mr-1 text-xs font-medium text-fg">
          {fieldLabel}
          {item.crop ? <span className="font-normal text-muted"> · {item.crop}</span> : null}
        </span>
        <span className="text-xs text-muted">farm {item.canonical_farm_id}</span>
        <span className="text-xs text-muted">· {formatDate(item.pass_date)}</span>
      </div>
      <div className="flex flex-wrap items-center gap-1.5">
        <Badge tone="neutral">{item.status}</Badge>
        <Badge tone="neutral">{item.confidence} confidence</Badge>
        {item.needs_review ? <Badge tone="caution">needs review</Badge> : null}
        {item.published ? <Badge tone="accent">published</Badge> : null}
      </div>
      <p className="line-clamp-3 text-sm leading-relaxed text-fg">{item.narrative}</p>
      <div className="flex flex-wrap items-center gap-2 pt-0.5">
        {item.published ? (
          <Button variant="outline" disabled={pending} onClick={() => submit(false)}>
            {pending ? "Working..." : "Unpublish"}
          </Button>
        ) : (
          <Button variant="primary" disabled={pending} onClick={() => submit(true)}>
            {pending ? "Publishing..." : "Approve & publish"}
          </Button>
        )}
        <Button variant="ghost" onClick={openField}>
          <ArrowSquareOut size={15} />
          Open field
        </Button>
      </div>
      {error ? <p className="text-xs text-critical">{error}</p> : null}
    </li>
  );
}
