import { Pause, Play } from "@phosphor-icons/react";
import { useEffect, useRef, useState } from "react";

import type { Scene } from "@/lib/api";

import { IconButton } from "./ui";

// ⚑ CONFIRM (backlog 0043): 1 pass/second, stop rather than loop at the end. Both are cheap to
// change later behind this same control - see docs/backlog/0043-as-of-date-timelapse-playback.md.
const PLAYBACK_INTERVAL_MS = 1000;

interface PlaybackControlProps {
  /** The field's passes, ascending by pass_date (MapPanel's `list`, already sorted server-side). */
  scenes: Scene[];
  passDate: string | null;
  setPassDate: (date: string | null) => void;
  /** True while the pass currently on screen still has a raster tile in flight - playback holds
   *  the frame instead of advancing past it (see useFieldMap's isRasterLoading). */
  isRasterLoading: boolean;
  fieldId: string | null;
  farmId: string | null;
  /** True whenever the map has left the single live view (side-by-side compare or the contact
   *  sheet grid) - both replace the raster this control animates and the loading signal that
   *  gates it, so an in-progress session stops rather than running ungoverned. */
  suspended: boolean;
  disabled: boolean;
  className?: string;
}

/** Play/pause control for the unified timeline: advances `passDate` through a field's passes in
 *  chronological order at a fixed interval, reusing the exact `setPassDate` wiring every other
 *  pass-pick control already drives (scrubber, list row, chart dot) - no new state shape (backlog
 *  0043). Lives in MapPanel's own control cluster rather than beside the scrubber because its
 *  "don't skip a loading frame" guarantee is sourced from the single map's own raster-loading
 *  signal; a sibling panel has no access to that without lifting the signal into shared state. */
export function PlaybackControl({
  scenes,
  passDate,
  setPassDate,
  isRasterLoading,
  fieldId,
  farmId,
  suspended,
  disabled,
  className,
}: PlaybackControlProps) {
  const [isPlaying, setIsPlaying] = useState(false);

  // Refs mirror the latest prop/state values so the ticking effect below can create a single
  // long-lived interval per play session (not tear down and recreate on every tick) while still
  // always reading fresh data. This also sidesteps setPassDate's identity changing on every
  // workspace state update (WorkspaceProvider recomputes its context value on every dispatch),
  // which would otherwise retrigger the effect and jitter the 1s cadence.
  const passDateRef = useRef(passDate);
  passDateRef.current = passDate;
  const setPassDateRef = useRef(setPassDate);
  setPassDateRef.current = setPassDate;
  const isRasterLoadingRef = useRef(isRasterLoading);
  isRasterLoadingRef.current = isRasterLoading;
  // The date playback itself most recently requested, so the "did the analyst just override this"
  // guard below can tell its own setPassDate calls apart from a manual pick.
  const lastAutoSetRef = useRef<string | null>(null);

  // Any manual pass pick - a scrubber tick, a list row, a chart dot - changes passDate to
  // something other than what playback itself last set. Stop immediately rather than fight the
  // analyst's own navigation.
  useEffect(() => {
    if (!isPlaying) return;
    if (passDate !== lastAutoSetRef.current) setIsPlaying(false);
  }, [passDate, isPlaying]);

  // A field/farm switch, or leaving the single-map view for compare or the contact sheet, always
  // stops playback - stated explicitly rather than relying on passDate incidentally going null,
  // so this holds even if that reducer behaviour ever changes.
  useEffect(() => {
    setIsPlaying(false);
  }, [fieldId, farmId, suspended]);

  // The timelapse itself: one interval per play session, ticking at a fixed cadence and reading
  // current position/loading state through the refs above rather than as effect dependencies.
  useEffect(() => {
    if (!isPlaying) return;
    const id = window.setInterval(() => {
      if (isRasterLoadingRef.current) return; // next tile still loading - hold this frame
      const currentIndex = scenes.findIndex((s) => s.pass_date === passDateRef.current);
      if (currentIndex === -1) {
        setIsPlaying(false);
        return;
      }
      const next = scenes[currentIndex + 1];
      if (!next) {
        setIsPlaying(false); // reached the last pass - stop, never loop back to the start
        return;
      }
      lastAutoSetRef.current = next.pass_date;
      setPassDateRef.current(next.pass_date);
    }, PLAYBACK_INTERVAL_MS);
    return () => window.clearInterval(id);
  }, [isPlaying, scenes]);

  const handleToggle = () => {
    if (isPlaying) {
      setIsPlaying(false);
      return;
    }
    const first = scenes[0];
    if (!first) return; // nothing to play; the button is disabled in this state anyway
    const currentIndex = passDate ? scenes.findIndex((s) => s.pass_date === passDate) : -1;
    // Resume forward from wherever the timeline currently sits. Restart from the first pass if
    // already at (or past) the last one - including the common case of no manual pick yet, which
    // the map shows as the latest pass by default - so Play is never a silent no-op.
    const atEnd = currentIndex === -1 || currentIndex >= scenes.length - 1;
    const start = atEnd ? first : (scenes[currentIndex] ?? first);
    lastAutoSetRef.current = start.pass_date;
    if (start.pass_date !== passDate) setPassDate(start.pass_date);
    setIsPlaying(true);
  };

  return (
    <IconButton
      label={isPlaying ? "Pause timelapse" : "Play timelapse"}
      active={isPlaying}
      onClick={handleToggle}
      disabled={disabled || scenes.length < 2}
      className={className}
    >
      {isPlaying ? <Pause size={18} /> : <Play size={18} />}
    </IconButton>
  );
}
