import type { IndexKey } from "./indices";

/** How the selected field's imagery is drawn on the map: the chosen spectral index as a
 *  colour-coded heatmap, a true-colour composite, a false-colour (NIR) composite, or nothing
 *  (basemap only). One explicit choice replaces three independent visibility toggles that used to
 *  override each other silently (a fcc > rgb > index cascade meant true colour always won), so the
 *  map always has a single, legible active view. */
export type MapView = "none" | "index" | "truecolor" | "falsecolor";

/** The tiler layer name a view renders (the `{index}` path segment), or null for basemap-only.
 *  Centralised so the pass reveal, the single map, and the side-by-side comparison can never
 *  disagree on what a given view actually draws. */
export function rasterForView(view: MapView, index: IndexKey): string | null {
  switch (view) {
    case "index":
      return index; // the selected spectral index heatmap (ndvi, evi2, ...)
    case "truecolor":
      return "rgb";
    case "falsecolor":
      return "fcc";
    case "none":
      return null;
  }
}
