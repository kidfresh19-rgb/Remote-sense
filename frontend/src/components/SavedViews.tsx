import { Star, X } from "@phosphor-icons/react";

import { cn } from "@/lib/format";
import { removeSavedView, useSavedViews } from "@/lib/savedViews";
import { useWorkspace } from "@/state/workspace";

import { IconButton } from "./ui";

/** Bookmarked views (saved AOIs), restored in one click. Persisted locally via the savedViews
 *  store; hidden entirely when empty so it never adds chrome to a fresh workspace. */
export function SavedViews() {
  const views = useSavedViews();
  const { fieldId, index, restoreView } = useWorkspace();

  if (!views.length) return null;

  return (
    <div className="shrink-0 border-t border-border">
      <div className="px-3 py-2 text-xs font-semibold uppercase tracking-wide text-muted">
        Saved views
      </div>
      <ul className="max-h-48 overflow-y-auto px-1 pb-2">
        {views.map((view) => {
          const active = view.fieldId === fieldId && view.index === index;
          return (
            <li key={view.id} className="flex items-center gap-1">
              <button
                onClick={() =>
                  restoreView({
                    farmId: view.canonicalFarmId,
                    fieldId: view.fieldId,
                    index: view.index,
                  })
                }
                className={cn(
                  "flex min-w-0 flex-1 items-center gap-2 rounded-md px-2 py-1.5 text-left text-sm ease-out transition-colors duration-150 focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-inset focus-visible:ring-accent",
                  active ? "bg-accent/15 text-accent" : "text-fg hover:bg-panel-2",
                )}
              >
                <Star size={14} weight="fill" className="shrink-0 text-accent" />
                <span className="min-w-0 flex-1 truncate">{view.label}</span>
                <span className="shrink-0 text-[10px] uppercase tracking-wide text-muted">
                  {view.index}
                </span>
              </button>
              <IconButton
                label={`Remove saved ${view.label} view`}
                onClick={() => removeSavedView(view.id)}
                className="shrink-0"
              >
                <X size={13} />
              </IconButton>
            </li>
          );
        })}
      </ul>
    </div>
  );
}
