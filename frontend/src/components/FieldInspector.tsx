import {
  Article,
  ChartLine,
  ClockCounterClockwise,
  Lightning,
  NotePencil,
  Star,
  Stack,
} from "@phosphor-icons/react";
import { useEffect, useRef, useState, type ReactNode } from "react";
import { motion, AnimatePresence } from "motion/react";

import { cn } from "@/lib/format";
import { useCollectField, useFields } from "@/lib/queries";
import { addSavedView, removeSavedView, useIsSaved } from "@/lib/savedViews";
import { useWorkspace } from "@/state/workspace";

import { AnnotationsPanel } from "./AnnotationsPanel";
import { AuditPanel } from "./AuditPanel";
import { IndexTimeseriesChart } from "./IndexTimeseriesChart";
import { InterpretationPanel } from "./InterpretationPanel";
import { SceneList } from "./SceneList";
import { EmptyState } from "./states";
import { Button, IconButton } from "./ui";

type Tab = "series" | "passes" | "read" | "notes" | "audit";

const TABS: { id: Tab; label: string; icon: ReactNode }[] = [
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

  // Reset the collecting indicator when the field changes; clear the safety timer on unmount.
  useEffect(() => {
    setCollecting(false);
    setCollectError(null);
    return () => {
      if (collectTimer.current) window.clearTimeout(collectTimer.current);
      collectTimer.current = null;
    };
  }, [fieldId]);

  const handleCollect = () => {
    if (!fieldId) return;
    setCollectError(null);
    collect.mutate(undefined, {
      onSuccess: () => {
        setCollecting(true);
        if (collectTimer.current) window.clearTimeout(collectTimer.current);
        // Stop polling after a few minutes so we never poll forever if no pass ever lands.
        collectTimer.current = window.setTimeout(() => setCollecting(false), 180_000);
      },
      onError: (err) =>
        setCollectError(err instanceof Error ? err.message : "Could not start collection."),
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
            label={saved ? "Remove saved view" : `Save ${index.toUpperCase()} view`}
            active={saved}
            onClick={toggleSaved}
          >
            <Star size={16} weight={saved ? "fill" : "regular"} />
          </IconButton>
        </div>
      </div>

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
            {tab === "series" ? (
              <IndexTimeseriesChart
                fieldId={fieldId}
                collecting={collecting}
                onCollect={handleCollect}
              />
            ) : tab === "passes" ? (
              <SceneList fieldId={fieldId} collecting={collecting} />
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
