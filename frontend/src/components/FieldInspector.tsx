import {
  Article,
  CalendarPlus,
  ChartLine,
  ClockCounterClockwise,
  Gauge,
  Lightning,
  NotePencil,
  Star,
  Stack,
} from "@phosphor-icons/react";
import { useEffect, useRef, useState, type ReactNode } from "react";
import { motion, AnimatePresence } from "motion/react";

import type { CollectDatesResult } from "@/lib/api";
import { cn } from "@/lib/format";
import { useCollectDates, useCollectField, useFields } from "@/lib/queries";
import { addSavedView, removeSavedView, useIsSaved } from "@/lib/savedViews";
import { useWorkspace } from "@/state/workspace";

import { AnnotationsPanel } from "./AnnotationsPanel";
import { AuditPanel } from "./AuditPanel";
import { DateBatchInput } from "./DateBatchInput";
import { FieldHealthPanel } from "./FieldHealthPanel";
import { IndexTimeseriesChart } from "./IndexTimeseriesChart";
import { InterpretationPanel } from "./InterpretationPanel";
import { SceneList } from "./SceneList";
import { EmptyState } from "./states";
import { Button, IconButton } from "./ui";

const MAX_COLLECT_DATES = 36; // mirrors MAX_COLLECT_DATES in services/api/workspace/fields.py

type Tab = "overview" | "series" | "passes" | "read" | "notes" | "audit";

const TABS: { id: Tab; label: string; icon: ReactNode }[] = [
  { id: "overview", label: "Overview", icon: <Gauge size={15} /> },
  { id: "series", label: "Series", icon: <ChartLine size={15} /> },
  { id: "passes", label: "Passes", icon: <Stack size={15} /> },
  { id: "read", label: "Read", icon: <Article size={15} /> },
  { id: "notes", label: "Notes", icon: <NotePencil size={15} /> },
  { id: "audit", label: "Audit", icon: <ClockCounterClockwise size={15} /> },
];

export function FieldInspector() {
  const { farmId, fieldId, index } = useWorkspace();
  const fields = useFields(farmId);
  const field = fields.data?.find((f) => f.field_id === fieldId) ?? null;
  const [tab, setTab] = useState<Tab>("series");
  const saved = useIsSaved(fieldId, index);

  const collect = useCollectField(fieldId);
  const [collecting, setCollecting] = useState(false);
  const [collectError, setCollectError] = useState<string | null>(null);
  const collectTimer = useRef<number | null>(null);

  // Targeted "collect specific dates": an inline panel beside "Collect now".
  const collectDates = useCollectDates(fieldId);
  const [datesOpen, setDatesOpen] = useState(false);
  const [batchDates, setBatchDates] = useState<string[]>([]);
  const [dateSummary, setDateSummary] = useState<CollectDatesResult | null>(null);

  // Reset the collecting indicator when the field changes; clear the safety timer on unmount.
  useEffect(() => {
    setCollecting(false);
    setCollectError(null);
    setDatesOpen(false);
    setBatchDates([]);
    setDateSummary(null);
    return () => {
      if (collectTimer.current) window.clearTimeout(collectTimer.current);
      collectTimer.current = null;
    };
  }, [fieldId]);

  const startCollectingWindow = () => {
    setCollecting(true);
    if (collectTimer.current) window.clearTimeout(collectTimer.current);
    // Stop polling after a few minutes so we never poll forever if no pass ever lands.
    collectTimer.current = window.setTimeout(() => setCollecting(false), 180_000);
  };

  const handleCollect = () => {
    if (!fieldId) return;
    setCollectError(null);
    collect.mutate(undefined, {
      onSuccess: () => startCollectingWindow(),
      onError: (err) =>
        setCollectError(err instanceof Error ? err.message : "Could not start collection."),
    });
  };

  const handleCollectDates = () => {
    if (!fieldId || batchDates.length === 0) return;
    setCollectError(null);
    setDateSummary(null);
    collectDates.mutate(batchDates, {
      onSuccess: (summary) => {
        setDateSummary(summary);
        setBatchDates([]);
        if (summary.enqueued > 0) startCollectingWindow();
      },
      onError: (err) =>
        setCollectError(err instanceof Error ? err.message : "Could not plan date collection."),
    });
  };

  if (!fieldId) {
    return (
      <aside className="hidden min-h-0 border-t border-border bg-panel lg:flex lg:flex-col lg:border-l lg:border-t-0">
        <EmptyState
          title="No field selected"
          hint="Field detail, time series, passes, the agronomic read, notes and provenance appear here."
        />
      </aside>
    );
  }

  const label = field?.name ?? field?.canonical_field_id ?? "Field";
  const toggleSaved = () => {
    if (!farmId) return;
    if (saved) removeSavedView(`${fieldId}::${index}`);
    else addSavedView({ label, canonicalFarmId: farmId, fieldId, index });
  };

  return (
    <aside className="flex min-h-0 flex-col border-t border-border bg-panel lg:border-l lg:border-t-0 h-full">
      <div className="flex items-start justify-between gap-2 border-b border-border px-3 py-2">
        <div className="min-w-0">
          <h2 className="truncate text-sm font-semibold text-fg">{label}</h2>
          <p className="truncate text-xs text-muted">
            {field?.crop ? `${field.crop} · ` : ""}geometry v{field?.geometry_version ?? "?"}
          </p>
          {collectError ? (
            <p className="truncate text-xs text-critical" title={collectError}>
              {collectError}
            </p>
          ) : null}
        </div>
        <div className="flex shrink-0 items-center gap-1.5">
          <Button
            variant="primary"
            onClick={handleCollect}
            disabled={collecting || collect.isPending}
            className="h-8 gap-1.5 px-2.5 text-xs"
            title="Fetch this field's satellite history now"
          >
            <Lightning size={13} weight="fill" />
            {collecting || collect.isPending ? "Collecting..." : "Collect now"}
          </Button>
          <IconButton
            label="Collect specific dates"
            active={datesOpen}
            onClick={() => setDatesOpen((o) => !o)}
            title="Collect a targeted batch of dates for this field"
          >
            <CalendarPlus size={16} />
          </IconButton>
          <IconButton
            label={saved ? "Remove saved view" : `Save ${index.toUpperCase()} view`}
            active={saved}
            onClick={toggleSaved}
          >
            <Star size={16} weight={saved ? "fill" : "regular"} />
          </IconButton>
        </div>
      </div>

      {datesOpen ? (
        <div className="flex flex-col gap-2 border-b border-border bg-panel-2/40 px-3 py-3">
          <div className="flex items-center justify-between">
            <span className="text-xs font-semibold uppercase tracking-wide text-muted">
              Collect dates ({batchDates.length})
            </span>
            <span className="text-[11px] text-muted">snaps to nearest pass · ±7 days</span>
          </div>
          <DateBatchInput dates={batchDates} onChange={setBatchDates} max={MAX_COLLECT_DATES} />
          <Button
            variant="primary"
            onClick={handleCollectDates}
            disabled={batchDates.length === 0 || collectDates.isPending}
            className="h-8 gap-1.5 px-2.5 text-xs"
          >
            <CalendarPlus size={13} weight="fill" />
            {collectDates.isPending
              ? "Planning…"
              : `Collect ${batchDates.length || ""} ${batchDates.length === 1 ? "date" : "dates"}`.trim()}
          </Button>
          {dateSummary ? (
            <div className="rounded-md border border-border bg-bg px-2.5 py-2 text-xs text-muted">
              <span className="font-medium text-fg">{dateSummary.enqueued}</span> enqueued ·{" "}
              {dateSummary.resolved.length} resolved · {dateSummary.skipped.length} skipped
              {dateSummary.skipped.length > 0 ? (
                <p className="mt-1 text-[11px] text-muted/80">
                  No pass within ±7 days: {dateSummary.skipped.join(", ")}
                </p>
              ) : null}
            </div>
          ) : null}
        </div>
      ) : null}

      <div
        role="tablist"
        aria-label="Field detail"
        className="flex overflow-x-auto border-b border-border px-1"
      >
        {TABS.map((t) => (
          <button
            key={t.id}
            role="tab"
            aria-selected={tab === t.id}
            onClick={() => setTab(t.id)}
            className={cn(
              "flex flex-1 items-center justify-center gap-1.5 whitespace-nowrap border-b-2 px-2 py-2 text-xs font-medium ease-out transition-colors duration-150 focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-inset focus-visible:ring-accent",
              tab === t.id ? "border-accent text-fg" : "border-transparent text-muted hover:text-fg",
            )}
          >
            {t.icon}
            {t.label}
          </button>
        ))}
      </div>

      <div className="min-h-0 flex-1 overflow-y-auto">
        <AnimatePresence mode="wait">
          <motion.div
            key={tab}
            initial={{ opacity: 0, y: 4 }}
            animate={{ opacity: 1, y: 0 }}
            exit={{ opacity: 0, y: -4 }}
            transition={{ duration: 0.12, ease: "easeOut" }}
            className="min-h-full flex flex-col"
          >
            {tab === "overview" ? (
              <FieldHealthPanel fieldId={fieldId} />
            ) : tab === "series" ? (
              <IndexTimeseriesChart
                fieldId={fieldId}
                collecting={collecting}
                onCollect={handleCollect}
              />
            ) : tab === "passes" ? (
              <SceneList
                fieldId={fieldId}
                collecting={collecting}
                geometryVersion={field?.geometry_version}
              />
            ) : tab === "read" ? (
              <InterpretationPanel fieldId={fieldId} />
            ) : tab === "notes" ? (
              <AnnotationsPanel fieldId={fieldId} />
            ) : (
              <AuditPanel fieldId={fieldId} />
            )}
          </motion.div>
        </AnimatePresence>
      </div>
    </aside>
  );
}
