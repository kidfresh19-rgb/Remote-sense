import { CaretRight, Folder, Plant } from "@phosphor-icons/react";

import type { Farm } from "@/lib/api";
import { cn } from "@/lib/format";
import { useFarms, useFields } from "@/lib/queries";
import { useWorkspace } from "@/state/workspace";

import { SavedViews } from "./SavedViews";
import { EmptyState, ErrorState, LoadingRows } from "./states";

const rowBase =
  "flex w-full items-center gap-2 rounded-md px-2 py-1.5 text-left text-sm ease-out transition-colors duration-150 focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-inset focus-visible:ring-accent";

export function FarmFieldSidebar() {
  const farms = useFarms();
  const { farmId, selectFarm } = useWorkspace();

  return (
    <aside className="flex min-h-0 flex-col border-b border-border bg-panel lg:border-b-0 lg:border-r">
      <div className="border-b border-border px-3 py-2 text-xs font-semibold uppercase tracking-wide text-muted">
        Farms
      </div>
      <div className="min-h-0 flex-1 overflow-y-auto lg:max-h-none">
        {farms.isLoading ? (
          <LoadingRows />
        ) : farms.isError ? (
          <ErrorState error={farms.error} onRetry={() => farms.refetch()} />
        ) : !farms.data?.length ? (
          <EmptyState title="No farms" hint="Ingested farms from the gateway appear here." />
        ) : (
          <ul className="p-1">
            {farms.data.map((farm) => (
              <FarmRow
                key={farm.canonical_farm_id}
                farm={farm}
                expanded={farm.canonical_farm_id === farmId}
                onSelect={() => selectFarm(farm.canonical_farm_id)}
              />
            ))}
          </ul>
        )}
      </div>
      <SavedViews />
    </aside>
  );
}

function FarmRow({
  farm,
  expanded,
  onSelect,
}: {
  farm: Farm;
  expanded: boolean;
  onSelect: () => void;
}) {
  return (
    <li>
      <button
        onClick={onSelect}
        aria-expanded={expanded}
        className={cn(rowBase, expanded ? "bg-panel-2 text-fg" : "text-fg hover:bg-panel-2")}
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
      </button>
      {expanded ? <FieldList canonicalFarmId={farm.canonical_farm_id} /> : null}
    </li>
  );
}

function FieldList({ canonicalFarmId }: { canonicalFarmId: string }) {
  const fields = useFields(canonicalFarmId);
  const { fieldId, selectField } = useWorkspace();

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
      {fields.data.map((field) => {
        const active = field.field_id === fieldId;
        return (
          <li key={field.field_id}>
            <button
              onClick={() => selectField(field.field_id)}
              className={cn(
                rowBase,
                active ? "bg-accent/15 text-accent" : "text-muted hover:bg-panel-2 hover:text-fg",
              )}
            >
              <Folder size={14} weight={active ? "fill" : "regular"} className="shrink-0" />
              <span className="min-w-0 flex-1 truncate">
                {field.name ?? field.canonical_field_id ?? "field"}
              </span>
              {field.crop ? (
                <span className="shrink-0 text-[10px] uppercase tracking-wide text-muted">
                  {field.crop}
                </span>
              ) : null}
            </button>
          </li>
        );
      })}
    </ul>
  );
}
