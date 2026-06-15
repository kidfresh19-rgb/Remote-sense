import { ArrowClockwise, CheckCircle, CloudArrowUp, SpinnerGap } from "@phosphor-icons/react";

import type { FarmPush } from "@/lib/useFarmPush";

import { Button } from "./ui";

/** Renders the current phase of a farm's gateway push (idle / sending / sent / recorded / retry).
 *  Presentation only - the state lives in `useFarmPush`, shared with the dashboard cards and modal. */
export function FarmPushButton({ push, className }: { push: FarmPush; className?: string }) {
  if (push.phase === "published") {
    return push.dryRun ? (
      <span
        className="flex items-center gap-1 text-xs font-medium text-muted"
        title="Recorded to the outbox but not sent: the gateway is in recording (dry-run) mode."
      >
        <CheckCircle size={14} />
        Recorded{push.resultCount ? ` · ${push.resultCount}` : ""}
      </span>
    ) : (
      <span className="flex items-center gap-1 text-xs font-medium text-positive">
        <CheckCircle size={14} weight="fill" />
        Sent{push.resultCount ? ` · ${push.resultCount}` : ""}
      </span>
    );
  }

  if (push.phase === "enqueuing" || push.phase === "polling") {
    return (
      <span className="flex items-center gap-1 text-xs font-medium text-accent">
        <SpinnerGap size={14} className="animate-spin" />
        {push.phase === "enqueuing" ? "Queuing…" : "Sending…"}
      </span>
    );
  }

  if (push.phase === "dead_letter" || push.phase === "enqueue_error") {
    return (
      <Button variant="outline" onClick={push.push} className={className}>
        <ArrowClockwise size={13} />
        Retry
      </Button>
    );
  }

  return (
    <Button variant="primary" onClick={push.push} className={className}>
      <CloudArrowUp size={14} />
      Push to gateway
    </Button>
  );
}
