import { ArrowSquareOut, CloudArrowUp, X } from "@phosphor-icons/react";
import type { UseMutationResult } from "@tanstack/react-query";
import { useEffect } from "react";

import type { AOIPushRequest, AOIPushResult, Farm } from "@/lib/api";
import { INDICES, type IndexKey } from "@/lib/indices";
import type { PushAllAOIResult } from "@/lib/queries";
import type { FarmPush } from "@/lib/useFarmPush";
import { FarmPushButton } from "@/components/FarmPushButton";
import { Button } from "@/components/ui";

export interface GatewaySendProps {
  /** Present when a whole-farm target is active; absent for custom-AOI mode. */
  farmPush: FarmPush | null;
  /** The index currently in view (custom-AOI mode only). */
  viewIndex: IndexKey;
  /** ok-pass count for `viewIndex` (custom-AOI mode only). */
  okPassCount: number;
  /** Every index job that has at least one ok pass. */
  pushableIndexJobs: { key: IndexKey; jobId: string; okCount: number }[];
  /** Sum of ok passes across all pushable index jobs. */
  totalOkPasses: number;
  /** Farm id bound to the farm <select>. */
  selectedFarmId: string;
  onSelectedFarmIdChange: (id: string) => void;
  /** List of farms for the picker (custom-AOI mode only). */
  farms: Farm[];
  /** Single-index push mutation. */
  push: UseMutationResult<AOIPushResult, Error, { jobId: string; req: AOIPushRequest }>;
  /** All-indices push mutation. */
  pushAll: UseMutationResult<
    PushAllAOIResult,
    Error,
    { jobIds: string[]; indexCount: number; canonicalFarmId: string }
  >;
  /** Job id for `viewIndex` (null when that index has not run). */
  viewedJobId: string | null;
  onClose: () => void;
}

/**
 * Shared "Send to gateway" surface used from the results-section header and the analysis report
 * footer. Encapsulates both modes:
 *   - Whole-farm: delegates entirely to FarmPushButton driven by the caller's useFarmPush state.
 *   - Custom-AOI: renders the farm picker, single-index and all-indices actions, and
 *     success/error feedback. Both exact "ok" passes and averaged "interpolated" passes are
 *     sent; only "no_pass" entries are excluded.
 *
 * All mutation hooks and farm-picker state are owned by Studio and passed in as props so there is
 * exactly one source of truth for pending/success/error across the results header and report modal.
 */
export function GatewaySendModal({
  farmPush,
  viewIndex,
  okPassCount,
  pushableIndexJobs,
  totalOkPasses,
  selectedFarmId,
  onSelectedFarmIdChange,
  farms,
  push,
  pushAll,
  viewedJobId,
  onClose,
}: GatewaySendProps) {
  useEffect(() => {
    const onKey = (e: KeyboardEvent) => {
      if (e.key === "Escape") onClose();
    };
    window.addEventListener("keydown", onKey);
    return () => window.removeEventListener("keydown", onKey);
  }, [onClose]);

  const pushOne = (jobId: string) => {
    if (!jobId || !selectedFarmId) return;
    push.mutate({ jobId, req: { canonical_farm_id: selectedFarmId } });
  };

  const handlePushAll = () => {
    if (!selectedFarmId || pushableIndexJobs.length === 0) return;
    pushAll.mutate({
      jobIds: pushableIndexJobs.map((j) => j.jobId),
      indexCount: pushableIndexJobs.length,
      canonicalFarmId: selectedFarmId,
    });
  };

  const canPushAll =
    !!selectedFarmId && pushableIndexJobs.length > 1 && !pushAll.isPending;

  const pushOneDisabled = !selectedFarmId || push.isPending;

  const labelFor = (key: IndexKey) => INDICES.find((m) => m.key === key)?.label ?? key;
  const indexLabel = labelFor(viewIndex);

  // When exactly one index is sendable, offer a single direct button for it (whatever index is in
  // view); with several, offer "Push all" plus an optional "only the viewed index" shortcut.
  const onlyPushable = pushableIndexJobs.length === 1 ? pushableIndexJobs[0] : null;

  const nothingPushable = !farmPush && pushableIndexJobs.length === 0;

  return (
    <div className="fixed inset-0 z-50 flex items-center justify-center bg-bg/60 p-4 backdrop-blur-sm sm:p-8">
      <div
        role="dialog"
        aria-modal="true"
        aria-label="Send to gateway"
        className="flex w-full max-w-sm flex-col overflow-hidden rounded-xl border border-border bg-panel shadow-xl"
      >
        {/* Header */}
        <div className="flex items-center justify-between gap-3 border-b border-border px-4 py-3">
          <div className="flex items-center gap-2 text-sm font-semibold text-fg">
            <CloudArrowUp size={18} className="text-accent" />
            <span>Send to gateway</span>
          </div>
          <button
            onClick={onClose}
            aria-label="Close"
            className="rounded-md p-1 text-muted transition-opacity hover:opacity-70"
          >
            <X size={16} />
          </button>
        </div>

        {/* Body */}
        <div className="flex flex-col gap-3 px-4 py-4">
          {/* Whole-farm mode */}
          {farmPush ? (
            <div className="flex flex-col gap-2">
              <p className="text-xs text-muted">
                Push all resolved farm passes to the gateway. The farm is already known from the
                analysis target.
              </p>
              <FarmPushButton push={farmPush} className="w-full gap-1.5" />
              {farmPush.error && (
                <p className="line-clamp-3 text-xs text-critical" title={farmPush.error}>
                  {farmPush.error}
                </p>
              )}
            </div>
          ) : nothingPushable ? (
            /* Nothing sendable yet */
            <p className="text-xs text-muted">
              No passes are ready to send yet. Run an analysis and wait for at least one
              index to finish.
            </p>
          ) : (
            /* Custom-AOI mode */
            <>
              <p className="text-xs text-muted">
                Attach these preview results to a farm on the gateway. Both exact and averaged
                (AVG) passes are sent.
              </p>

              <select
                value={selectedFarmId}
                onChange={(e) => {
                  onSelectedFarmIdChange(e.target.value);
                  push.reset();
                  pushAll.reset();
                }}
                className="w-full rounded-md border border-border bg-bg px-2.5 py-1.5 text-xs text-fg focus:outline-none focus:ring-2 focus:ring-accent"
                aria-label="Farm to attach results to"
              >
                <option value="">Select a farm…</option>
                {farms.map((f) => (
                  <option key={f.canonical_farm_id} value={f.canonical_farm_id}>
                    {f.name ?? f.canonical_farm_id}
                  </option>
                ))}
              </select>

              {!selectedFarmId && (
                <p className="text-[11px] text-muted">Select a farm to enable the send actions.</p>
              )}

              <div className="flex flex-col gap-2">
                {onlyPushable ? (
                  <Button
                    variant="primary"
                    onClick={() => pushOne(onlyPushable.jobId)}
                    disabled={pushOneDisabled}
                    className="w-full gap-1.5"
                  >
                    <CloudArrowUp size={14} />
                    {push.isPending
                      ? "Pushing…"
                      : `Push ${labelFor(onlyPushable.key)} · ${onlyPushable.okCount} ${onlyPushable.okCount === 1 ? "pass" : "passes"}`}
                  </Button>
                ) : (
                  <>
                    <Button
                      variant="primary"
                      onClick={handlePushAll}
                      disabled={!canPushAll}
                      className="w-full gap-1.5"
                    >
                      <CloudArrowUp size={14} />
                      {pushAll.isPending
                        ? "Pushing all…"
                        : `Push all ${pushableIndexJobs.length} indices · ${totalOkPasses} ${totalOkPasses === 1 ? "pass" : "passes"}`}
                    </Button>

                    {okPassCount > 0 && viewedJobId && (
                      <Button
                        variant="outline"
                        onClick={() => pushOne(viewedJobId)}
                        disabled={pushOneDisabled}
                        className="w-full gap-1.5"
                      >
                        <ArrowSquareOut size={14} />
                        {push.isPending
                          ? "Pushing…"
                          : `Push only ${indexLabel} · ${okPassCount} ${okPassCount === 1 ? "pass" : "passes"}`}
                      </Button>
                    )}
                  </>
                )}
              </div>

              {/* Single-index feedback */}
              {push.isSuccess && (
                <p className="text-xs text-positive">
                  {push.data.dry_run
                    ? `Recorded ${push.data.pushed_passes} passes (dry-run)`
                    : `Sent ${push.data.pushed_passes} passes`}
                </p>
              )}
              {push.isError && (
                <p className="text-xs text-critical">
                  {push.error instanceof Error ? push.error.message : "Push failed"}
                </p>
              )}

              {/* All-indices feedback */}
              {pushAll.isSuccess && (
                <p className="text-xs text-positive">
                  {pushAll.data.dryRun
                    ? `Recorded ${pushAll.data.pushedPasses} passes across ${pushAll.data.indices} indices (dry-run)`
                    : `Sent ${pushAll.data.pushedPasses} passes across ${pushAll.data.indices} indices`}
                </p>
              )}
              {pushAll.isError && (
                <p className="text-xs text-critical">
                  {pushAll.error instanceof Error ? pushAll.error.message : "Push failed"}
                </p>
              )}
            </>
          )}
        </div>

        {/* Footer */}
        <div className="flex justify-end border-t border-border px-4 py-3">
          <Button variant="outline" onClick={onClose} className="gap-1.5">
            Close
          </Button>
        </div>
      </div>
    </div>
  );
}
