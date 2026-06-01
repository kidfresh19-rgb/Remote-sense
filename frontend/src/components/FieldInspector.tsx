import {
  Article,
  ChartLine,
  ClockCounterClockwise,
  NotePencil,
  Star,
  Stack,
} from "@phosphor-icons/react";
import { useState, type ReactNode } from "react";

import { cn } from "@/lib/format";
import { useFields } from "@/lib/queries";
import { addSavedView, removeSavedView, useIsSaved } from "@/lib/savedViews";
import { useWorkspace } from "@/state/workspace";

import { AnnotationsPanel } from "./AnnotationsPanel";
import { AuditPanel } from "./AuditPanel";
import { IndexTimeseriesChart } from "./IndexTimeseriesChart";
import { InterpretationPanel } from "./InterpretationPanel";
import { SceneList } from "./SceneList";
import { EmptyState } from "./states";
import { IconButton } from "./ui";

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
    <aside className="flex min-h-0 flex-col border-t border-border bg-panel lg:border-l lg:border-t-0">
      <div className="flex items-start justify-between gap-2 border-b border-border px-3 py-2">
        <div className="min-w-0">
          <h2 className="truncate text-sm font-semibold text-fg">{label}</h2>
          <p className="truncate text-xs text-muted">
            {field?.crop ? `${field.crop} · ` : ""}geometry v{field?.geometry_version ?? "?"}
          </p>
        </div>
        <IconButton
          label={saved ? "Remove saved view" : `Save ${index.toUpperCase()} view`}
          active={saved}
          onClick={toggleSaved}
          className="shrink-0"
        >
          <Star size={16} weight={saved ? "fill" : "regular"} />
        </IconButton>
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
        {tab === "series" ? (
          <IndexTimeseriesChart fieldId={fieldId} />
        ) : tab === "passes" ? (
          <SceneList fieldId={fieldId} />
        ) : tab === "read" ? (
          <InterpretationPanel fieldId={fieldId} />
        ) : tab === "notes" ? (
          <AnnotationsPanel fieldId={fieldId} />
        ) : (
          <AuditPanel fieldId={fieldId} />
        )}
      </div>
    </aside>
  );
}
