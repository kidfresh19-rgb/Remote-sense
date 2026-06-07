import { cn, formatDate } from "@/lib/format";
import { useScenes } from "@/lib/queries";
import { useWorkspace } from "@/state/workspace";

import { EmptyState, ErrorState, LoadingRows } from "./states";
import { TimelineScrubber } from "./TimelineScrubber";

export function SceneList({
  fieldId,
  collecting = false,
}: {
  fieldId: string;
  collecting?: boolean;
}) {
  const query = useScenes(fieldId, collecting);
  const { passDate, setPassDate } = useWorkspace();

  if (query.isLoading) return <LoadingRows />;
  if (query.isError) return <ErrorState error={query.error} onRetry={() => query.refetch()} />;
  const scenes = query.data ?? [];
  if (!scenes.length) {
    return (
      <EmptyState
        title="No passes"
        hint={
          collecting
            ? "Collecting passes. They appear here as each scene is processed."
            : "No collected passes for this field yet."
        }
      />
    );
  }

  return (
    <div className="flex flex-col">
      <div className="border-b border-border p-3">
        <TimelineScrubber
          dates={scenes.map((s) => s.pass_date)}
          value={passDate}
          onChange={setPassDate}
        />
      </div>
      <ul className="divide-y divide-border">
        {[...scenes].reverse().map((scene) => {
          const active = scene.pass_date === passDate;
          return (
            <li key={scene.scene_id}>
              <button
                onClick={() => setPassDate(scene.pass_date)}
                className={cn(
                  "flex w-full items-center justify-between gap-3 px-3 py-2 text-left ease-out transition-colors duration-150 focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-inset focus-visible:ring-accent",
                  active ? "bg-accent/10" : "hover:bg-panel-2",
                )}
              >
                <div className="min-w-0">
                  <p className="text-sm text-fg">{formatDate(scene.pass_date)}</p>
                  <p className="truncate font-mono text-[11px] text-muted">{scene.scene_id}</p>
                </div>
                {active ? <span className="size-2 shrink-0 rounded-full bg-accent" /> : null}
              </button>
            </li>
          );
        })}
      </ul>
    </div>
  );
}
