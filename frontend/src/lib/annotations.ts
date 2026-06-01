import { useSyncExternalStore } from "react";

/** A free-text note an analyst pins to a field (optionally to a specific pass).
 *
 *  ⚑ CONFIRM: notes persist to this browser only. The shared, team-visible store, a write endpoint
 *  behind the RBAC `annotate` permission plus an `annotation` table, is a parked backend decision.
 *  This module is the seam: swapping the localStorage adapter below for a server-backed one leaves
 *  every call site (AnnotationsPanel) untouched. Until then the panel is honest that notes are local. */
export interface Annotation {
  id: string;
  fieldId: string;
  geometryVersion: number;
  passDate: string | null;
  body: string;
  author: string | null;
  createdAt: number;
}

const KEY = "rs-annotations";

function read(): Annotation[] {
  try {
    const raw = localStorage.getItem(KEY);
    const parsed = raw ? JSON.parse(raw) : [];
    return Array.isArray(parsed) ? (parsed as Annotation[]) : [];
  } catch {
    return [];
  }
}

let cache: Annotation[] = read();
const listeners = new Set<() => void>();

function write(next: Annotation[]): void {
  cache = next;
  try {
    localStorage.setItem(KEY, JSON.stringify(next));
  } catch {
    // Quota or privacy-mode failure: keep the in-memory cache so the session still works.
  }
  for (const listener of listeners) listener();
}

function newId(): string {
  if (typeof crypto !== "undefined" && "randomUUID" in crypto) return crypto.randomUUID();
  return `${Date.now()}-${Math.random().toString(36).slice(2)}`;
}

export function addAnnotation(input: Omit<Annotation, "id" | "createdAt">): void {
  write([{ ...input, id: newId(), createdAt: Date.now() }, ...cache]);
}

export function removeAnnotation(id: string): void {
  write(cache.filter((a) => a.id !== id));
}

function subscribe(listener: () => void): () => void {
  listeners.add(listener);
  return () => listeners.delete(listener);
}

/** Notes for one field, newest first. Filtered from the shared cache; the empty array for a field
 *  with no notes is a stable reference so useSyncExternalStore does not loop. */
const EMPTY: Annotation[] = [];
const byField = new Map<string, Annotation[]>();
let memoSource: Annotation[] = cache;

function annotationsFor(fieldId: string): Annotation[] {
  if (memoSource !== cache) {
    byField.clear();
    memoSource = cache;
  }
  const cached = byField.get(fieldId);
  if (cached) return cached;
  const next = cache.filter((a) => a.fieldId === fieldId);
  const result = next.length ? next : EMPTY;
  byField.set(fieldId, result);
  return result;
}

export function useAnnotations(fieldId: string | null): Annotation[] {
  return useSyncExternalStore(
    subscribe,
    () => (fieldId ? annotationsFor(fieldId) : EMPTY),
    () => (fieldId ? annotationsFor(fieldId) : EMPTY),
  );
}
