import type { Geometry } from "geojson";
import { useSyncExternalStore } from "react";

export interface CustomAOI {
  id: string;
  label: string;
  geometry: Geometry;
  createdAt: string; // ISO string
}

const KEY = "rs-custom-aois";

function read(): CustomAOI[] {
  try {
    const raw = localStorage.getItem(KEY);
    const parsed = raw ? JSON.parse(raw) : [];
    return Array.isArray(parsed) ? (parsed as CustomAOI[]) : [];
  } catch {
    return [];
  }
}

// Module-level store — mirrors the pattern in savedViews.ts exactly so all consumers stay in
// sync without prop drilling and without a React context.
let cache: CustomAOI[] = read();
const listeners = new Set<() => void>();

function write(next: CustomAOI[]): void {
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

function snapshot(): CustomAOI[] {
  return cache;
}

export function getCustomAOIs(): CustomAOI[] {
  return cache;
}

export function saveCustomAOI(aoi: Omit<CustomAOI, "id" | "createdAt">): CustomAOI {
  const id = crypto.randomUUID();
  const createdAt = new Date().toISOString();
  const next: CustomAOI = { ...aoi, id, createdAt };
  write([next, ...cache]);
  return next;
}

export function deleteCustomAOI(id: string): void {
  write(cache.filter((a) => a.id !== id));
}

export function useCustomAOIs(): CustomAOI[] {
  return useSyncExternalStore(subscribe, snapshot, snapshot);
}
