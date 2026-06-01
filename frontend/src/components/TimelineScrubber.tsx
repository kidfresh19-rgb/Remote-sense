import { cn, dateValue, formatDate } from "@/lib/format";

/** A single horizontal axis of every pass date for the field, true to spacing in time. Clicking a
 *  tick selects that pass, which drives the chart highlight and (once the raster lands) the map. */
export function TimelineScrubber({
  dates,
  value,
  onChange,
}: {
  dates: string[];
  value: string | null;
  onChange: (date: string) => void;
}) {
  if (!dates.length) return null;
  const xs = dates.map(dateValue);
  const min = Math.min(...xs);
  const max = Math.max(...xs);
  const span = max - min || 1;

  return (
    <div>
      <div className="mb-1 flex items-center justify-between text-[10px] uppercase tracking-wide text-muted">
        <span>Timeline</span>
        <span className="tnum normal-case">{value ? formatDate(value) : `${dates.length} passes`}</span>
      </div>
      <div className="relative h-7">
        <div className="absolute inset-x-0 top-1/2 h-px -translate-y-1/2 bg-border" />
        {dates.map((date, i) => {
          const left = ((xs[i] - min) / span) * 100;
          const active = date === value;
          return (
            <button
              key={date}
              aria-label={formatDate(date)}
              title={formatDate(date)}
              onClick={() => onChange(date)}
              style={{ left: `${left}%` }}
              className={cn(
                "absolute top-1/2 -translate-x-1/2 -translate-y-1/2 rounded-full ease-out transition-transform duration-150 focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-accent",
                active ? "size-3 bg-accent" : "size-2 bg-muted hover:scale-150",
              )}
            />
          );
        })}
      </div>
    </div>
  );
}
