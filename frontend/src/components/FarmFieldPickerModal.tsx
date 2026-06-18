import { CaretRight, Crosshair, MagnifyingGlass, Plant, UsersThree, X } from "@phosphor-icons/react";
import { AnimatePresence, motion } from "motion/react";
import { useEffect, useMemo, useState } from "react";

import type { Farm, Field } from "@/lib/api";
import { cn } from "@/lib/format";
import { useFarms, useFields } from "@/lib/queries";

import { EmptyState, ErrorState, LoadingRows } from "./states";
import { Badge } from "./ui";

interface FarmFieldPickerModalProps {
  onClose: () => void;
  /** Use a single field's boundary as the AOI. */
  onPickField: (field: Field) => void;
  /** Use the entire farm (union of all fields) as the AOI via the server-side engine. */
  onPickFarm: (farm: Farm) => void;
}

const rowBase =
  "flex w-full items-center gap-2 rounded-md px-2 py-1.5 text-left text-sm ease-out transition-colors duration-150 focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-inset focus-visible:ring-accent";

/** Pick an already-ingested farm or field as the Studio's AOI. Two selection modes:
 *  1. "Use whole farm" — triggers the server-side farm series endpoint that unions all field
 *     geometries (no drawing required; Slice 2 backend).
 *  2. "Use field" — picks a single field's stored boundary as the AOI (original Slice 1).
 *  The farm/field tree mirrors FarmFieldSidebar; the modal shell mirrors FileUploadPanel. */
export function FarmFieldPickerModal({ onClose, onPickField, onPickFarm }: FarmFieldPickerModalProps) {
  const farms = useFarms();
  const [query, setQuery] = useState("");
  const [expanded, setExpanded] = useState<Set<string>>(new Set());

  useEffect(() => {
    function onKey(e: KeyboardEvent) {
      if (e.key === "Escape") onClose();
    }
    document.addEventListener("keydown", onKey);
    return () => document.removeEventListener("keydown", onKey);
  }, [onClose]);

  const filtered = useMemo(() => {
    const q = query.trim().toLowerCase();
    if (!q || !farms.data) return farms.data ?? [];
    return farms.data.filter((f) =>
      (f.name ?? f.canonical_farm_id).toLowerCase().includes(q),
    );
  }, [farms.data, query]);

  function toggle(id: string) {
    setExpanded((prev) => {
      const next = new Set(prev);
      if (next.has(id)) next.delete(id);
      else next.add(id);
      return next;
    });
  }

  function handlePickField(field: Field) {
    onPickField(field);
    onClose();
  }

  function handlePickFarm(farm: Farm) {
    onPickFarm(farm);
    onClose();
  }

  return (
    <div className="absolute inset-0 z-40 flex items-center justify-center bg-bg/60 backdrop-blur-sm">
      <div
        role="dialog"
        aria-modal="true"
        aria-labelledby="farm-picker-title"
        className="relative flex max-h-[80%] w-full max-w-md flex-col rounded-xl border border-border bg-panel shadow-xl"
      >
        {/* Header */}
        <div className="flex items-center justify-between border-b border-border px-4 py-3">
          <h2 id="farm-picker-title" className="text-sm font-semibold text-fg">
            Use a farm or field
          </h2>
          <button
            onClick={onClose}
            aria-label="Close"
            className="rounded-md p-1 text-muted transition-colors hover:bg-panel-2 hover:text-fg"
          >
            <X size={16} />
          </button>
        </div>

        {/* Farm filter */}
        <div className="border-b border-border px-3 py-2">
          <div className="flex items-center gap-2 rounded-md border border-border bg-bg px-2 py-1.5">
            <MagnifyingGlass size={14} className="shrink-0 text-muted" />
            <input
              type="text"
              value={query}
              onChange={(e) => setQuery(e.target.value)}
              placeholder="Filter farms..."
              aria-label="Filter farms by name"
              className="min-w-0 flex-1 bg-transparent text-sm text-fg placeholder:text-muted focus:outline-none"
            />
            {query ? (
              <button
                onClick={() => setQuery("")}
                aria-label="Clear filter"
                className="shrink-0 text-muted transition-colors hover:text-fg"
              >
                <X size={13} />
              </button>
            ) : null}
          </div>
        </div>

        {/* Tree */}
        <div className="min-h-[200px] flex-1 overflow-y-auto">
          {farms.isLoading ? (
            <LoadingRows />
          ) : farms.isError ? (
            <ErrorState error={farms.error} onRetry={() => farms.refetch()} />
          ) : !farms.data?.length ? (
            <EmptyState title="No farms" hint="Ingested farms from the gateway appear here." />
          ) : !filtered.length ? (
            <EmptyState title="No matching farms" hint="Try a different name." />
          ) : (
            <ul className="p-1">
              {filtered.map((farm) => (
                <FarmRow
                  key={farm.canonical_farm_id}
                  farm={farm}
                  expanded={expanded.has(farm.canonical_farm_id)}
                  onToggle={() => toggle(farm.canonical_farm_id)}
                  onPickField={handlePickField}
                  onPickFarm={handlePickFarm}
                />
              ))}
            </ul>
          )}
        </div>

        {/* Footer */}
        <div className="border-t border-border px-4 py-2.5">
          <p className="text-[11px] leading-relaxed text-muted">
            Click{" "}
            <span className="inline-flex items-center gap-0.5 font-medium text-accent">
              <UsersThree size={11} /> Whole farm
            </span>{" "}
            to analyse all fields as one area, or expand a farm and choose a single field.
          </p>
        </div>
      </div>
    </div>
  );
}

function FarmRow({
  farm,
  expanded,
  onToggle,
  onPickField,
  onPickFarm,
}: {
  farm: Farm;
  expanded: boolean;
  onToggle: () => void;
  onPickField: (field: Field) => void;
  onPickFarm: (farm: Farm) => void;
}) {
  return (
    <li>
      <div className={cn("flex items-center gap-1 rounded-md", expanded && "bg-panel-2")}>
        {/* Expand/collapse */}
        <button
          onClick={onToggle}
          aria-expanded={expanded}
          aria-label={`${expanded ? "Collapse" : "Expand"} ${farm.name ?? farm.canonical_farm_id}`}
          className={cn(
            rowBase,
            "flex-1",
            expanded ? "text-fg" : "text-fg hover:bg-panel-2",
          )}
        >
          <CaretRight
            size={14}
            weight="bold"
            className={cn(
              "shrink-0 text-muted transition-transform duration-150 ease-out",
              expanded && "rotate-90",
            )}
          />
          <Plant size={16} weight="duotone" className="shrink-0 text-muted" />
          <span className="min-w-0 flex-1 truncate">{farm.name ?? farm.canonical_farm_id}</span>
          {farm.total_fields ? (
            <span className="shrink-0 text-[10px] uppercase tracking-wide text-muted">
              {farm.total_fields} {farm.total_fields === 1 ? "field" : "fields"}
            </span>
          ) : null}
        </button>
        {/* Whole-farm shortcut */}
        <button
          onClick={() => onPickFarm(farm)}
          title={`Use whole farm: ${farm.name ?? farm.canonical_farm_id}`}
          aria-label={`Use whole farm: ${farm.name ?? farm.canonical_farm_id}`}
          className="mr-1 flex shrink-0 items-center gap-1 rounded-md border border-border px-2 py-1 text-[10px] uppercase tracking-wide text-accent transition-colors hover:bg-accent/10 focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-accent"
        >
          <UsersThree size={12} />
          Whole farm
        </button>
      </div>
      <AnimatePresence initial={false}>
        {expanded && (
          <motion.div
            initial={{ height: 0, opacity: 0 }}
            animate={{ height: "auto", opacity: 1 }}
            exit={{ height: 0, opacity: 0 }}
            transition={{ duration: 0.15, ease: "easeOut" }}
            className="overflow-hidden"
          >
            <FieldList canonicalFarmId={farm.canonical_farm_id} onPickField={onPickField} />
          </motion.div>
        )}
      </AnimatePresence>
    </li>
  );
}

function FieldList({
  canonicalFarmId,
  onPickField,
}: {
  canonicalFarmId: string;
  onPickField: (field: Field) => void;
}) {
  const fields = useFields(canonicalFarmId);

  if (fields.isLoading) {
    return (
      <div className="pl-6">
        <LoadingRows rows={2} />
      </div>
    );
  }
  if (fields.isError) {
    return <p className="px-7 py-1.5 text-xs text-critical">Failed to load fields</p>;
  }
  if (!fields.data?.length) {
    return <p className="px-7 py-1.5 text-xs text-muted">No fields</p>;
  }

  return (
    <ul className="mb-1 ml-[18px] border-l border-border pl-1">
      {fields.data.map((field) => (
        <li key={field.field_id}>
          <button
            onClick={() => onPickField(field)}
            className={cn(rowBase, "text-muted hover:bg-panel-2 hover:text-fg")}
          >
            <span className="min-w-0 flex-1 truncate">
              {field.name ?? field.canonical_field_id ?? "field"}
            </span>
            {field.crop ? (
              <Badge tone="neutral" className="shrink-0 text-[9px] uppercase">
                {field.crop}
              </Badge>
            ) : null}
            <span className="flex shrink-0 items-center gap-1 text-[10px] uppercase tracking-wide text-accent">
              <Crosshair size={12} /> Use field
            </span>
          </button>
        </li>
      ))}
    </ul>
  );
}
