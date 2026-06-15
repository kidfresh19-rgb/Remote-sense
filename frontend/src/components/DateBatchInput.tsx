import { CalendarBlank, Plus, X } from "@phosphor-icons/react";
import { useState } from "react";

import { formatDate } from "@/lib/format";

import { IconButton } from "./ui";

const todayIso = () => new Date().toISOString().slice(0, 10);

/** A capped batch of calendar dates as removable chips. Shared by AOI Studio (cap 24) and the
 *  field "collect dates" action (cap 36); future dates and duplicates are rejected on add, and the
 *  list is kept sorted. The parent owns any surrounding label and mode-specific helper text. */
export function DateBatchInput({
  dates,
  onChange,
  max,
}: {
  dates: string[];
  onChange: (next: string[]) => void;
  max: number;
}) {
  const [draft, setDraft] = useState("");
  const atCap = dates.length >= max;

  const add = () => {
    if (!draft || atCap) return;
    if (draft > todayIso()) return; // no future dates (the API rejects them too)
    if (!dates.includes(draft)) onChange([...dates, draft].sort());
    setDraft("");
  };

  return (
    <div className="flex flex-col gap-2">
      <div className="flex items-center gap-1.5">
        <input
          type="date"
          value={draft}
          max={todayIso()}
          onChange={(e) => setDraft(e.target.value)}
          onKeyDown={(e) => {
            if (e.key === "Enter") {
              e.preventDefault();
              add();
            }
          }}
          aria-label="Add a date"
          className="min-w-0 flex-1 rounded-md border border-border bg-bg px-2 py-1.5 text-sm text-fg outline-none focus-visible:ring-2 focus-visible:ring-accent"
        />
        <IconButton
          label="Add date"
          onClick={add}
          disabled={!draft || atCap}
          className="size-9 border border-border bg-panel"
        >
          <Plus size={15} />
        </IconButton>
      </div>
      {atCap ? <p className="text-[11px] text-caution">Up to {max} dates per batch.</p> : null}
      {dates.length > 0 ? (
        <ul className="flex flex-wrap gap-1.5">
          {dates.map((d) => (
            <li key={d}>
              <button
                onClick={() => onChange(dates.filter((x) => x !== d))}
                className="inline-flex items-center gap-1 rounded-full border border-border bg-bg px-2 py-0.5 text-xs text-fg transition-colors hover:border-critical/50 hover:text-critical"
                aria-label={`Remove ${formatDate(d)}`}
              >
                <CalendarBlank size={11} />
                {formatDate(d)}
                <X size={11} />
              </button>
            </li>
          ))}
        </ul>
      ) : null}
    </div>
  );
}
