import { useSyncExternalStore } from "react";

import type { IndexKey } from "./indices";

/** A bookmarked workspace view (a saved AOI): a field plus the index the analyst was looking at.
 *  Persisted locally to the browser. The read/write surface is deliberately small so a future
 *  server-backed store (shared across analysts) can replace it without touching call sites. */
export interface SavedView {
  id: string;
  label: string;
  canonicalFarmId: string;
  fieldId: string;
  index: IndexKey;
  createdAt: number;
}

const KEY = "rs-saved-views";

function read(): SavedView[] {
  try {
    const raw = localStorage.getItem(KEY);
    const parsed = raw ? JSON.parse(raw) : [];
    return Array.isArray(parsed) ? (parsed as SavedView[]) : [];
  } catch {
    return [];
  }
}

// Module-level store so the sidebar list and the save toggle stay in sync without prop drilling.
let cache: SavedView[] = read();
const listeners = new Set<() => void>();

function write(next: SavedView[]): void {
  cache = next;
  try {
    localStorage.setItem(KEY, JSON.stringify(next));
  } catch {
    // Quota or privacy-mode failure: keep the in-memory cache so the session still works.
  }
  for (const listener of listeners) listener();
}

function subscribe(listener: () => void): () => void {
  listeners.add(listener);
  return () => listeners.delete(listener);
}

function snapshot(): SavedView[] {
  return cache;
}

function viewId(fieldId: string, index: IndexKey): string {
  return `${fieldId}::${index}`;
}

export function addSavedView(view: Omit<SavedView, "id" | "createdAt">): void {
  const id = viewId(view.fieldId, view.index);
  // De-dup on (field, index) so re-saving refreshes the entry instead of stacking duplicates.
  const rest = cache.filter((v) => v.id !== id);
  write([{ ...view, id, createdAt: Date.now() }, ...rest]);
}

export function removeSavedView(id: string): void {
  write(cache.filter((v) => v.id !== id));
}

export function useSavedViews(): SavedView[] {
  return useSyncExternalStore(subscribe, snapshot, snapshot);
}

export function useIsSaved(fieldId: string | null, index: IndexKey): boolean {
  const views = useSavedViews();
  return fieldId !== null && views.some((v) => v.id === viewId(fieldId, index));
}
