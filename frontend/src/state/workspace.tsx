import type { Geometry } from "geojson";
import { createContext, useContext, useMemo, useReducer, type ReactNode } from "react";

import { DEFAULT_INDEX, type IndexKey } from "@/lib/indices";
import type { MapView } from "@/lib/mapView";

interface WorkspaceState {
  farmId: string | null;
  fieldId: string | null;
  index: IndexKey;
  passDate: string | null;
  // The as-of date request (S3.1). Non-null while the current passDate was chosen by date
  // resolution, so the UI can keep showing "requested X -> showing Y". A manual pass pick or a
  // farm/field change retires it.
  requestedDate: string | null;
  // The second pass to compare the primary against. Non-null puts the map into side-by-side mode.
  compareDate: string | null;
  // How the field imagery is drawn on the map: the selected index heatmap, true colour, false
  // colour (NIR), or basemap only. A single explicit view, so the layers can never silently
  // override one another.
  mapView: MapView;
  customAOI: Geometry | null; // user-defined analysis boundary
  // Region-boundary map overlays (comparison groups, PRD 0002 slices 8a/8b). Global map context,
  // independent of the selected field, so they are not cleared on a farm/field change.
  showNaturalRegions: boolean; // the seeded Natural Region layer
  uploadedRegionLayerId: string | null; // the chosen analyst-uploaded layer, or none
}

interface RestoreView {
  farmId: string;
  fieldId: string;
  index: IndexKey;
}

type Action =
  | { type: "selectFarm"; farmId: string }
  | { type: "selectField"; fieldId: string }
  | { type: "setIndex"; index: IndexKey }
  | { type: "setPassDate"; passDate: string | null }
  | { type: "setRequestedDate"; requestedDate: string | null }
  | { type: "applyResolvedPass"; passDate: string }
  | { type: "setCompareDate"; compareDate: string | null }
  | { type: "restoreView"; view: RestoreView }
  | { type: "setMapView"; view: MapView }
  | { type: "setCustomAOI"; geometry: Geometry | null }
  | { type: "toggleNaturalRegions" }
  | { type: "setUploadedRegionLayer"; layerId: string | null };

const initialState: WorkspaceState = {
  farmId: null,
  fieldId: null,
  index: DEFAULT_INDEX,
  passDate: null,
  requestedDate: null,
  compareDate: null,
  mapView: "none",
  customAOI: null,
  showNaturalRegions: false,
  uploadedRegionLayerId: null,
};

/** Reveal the current pass on the map. The map only draws imagery while a view (true colour / false
 *  colour / index heatmap) is active, and it defaults to basemap-only, so selecting a pass would
 *  otherwise leave the map unchanged. When a pass is in play and the map is still basemap-only,
 *  default to true colour - how the field actually looked on that day - which is what an analyst
 *  reaches for first. The index heatmap and false colour are one click away on the view control.
 *  An already-chosen view is left untouched. Imagery is never averaged or synthesised for a gap: a
 *  date the satellite skipped resolves to the nearest stored pass upstream (invariant 4), and this
 *  only decides how that pass is drawn. */
function revealPass(state: WorkspaceState): WorkspaceState {
  if (state.passDate && state.mapView === "none") {
    return { ...state, mapView: "truecolor" };
  }
  return state;
}

function reducer(state: WorkspaceState, action: Action): WorkspaceState {
  switch (action.type) {
    case "selectFarm":
      if (action.farmId === state.farmId) return state;
      // Switching farm clears the field-scoped selection so panels never show stale context. The
      // map view persists: an analyst working in the index heatmap keeps it as they move fields.
      return {
        ...state,
        farmId: action.farmId,
        fieldId: null,
        passDate: null,
        requestedDate: null,
        compareDate: null,
      };
    case "selectField":
      if (action.fieldId === state.fieldId) return state;
      return {
        ...state,
        fieldId: action.fieldId,
        passDate: null,
        requestedDate: null,
        compareDate: null,
      };
    case "setIndex":
      // Picking an index sets which index the heatmap view draws; it does not change the view. So
      // choosing NDVI while viewing the true-colour photo leaves the photo up (the heatmap updates
      // when the analyst switches to the index view), and switching index while in the heatmap
      // re-renders it.
      return { ...state, index: action.index };
    case "setPassDate":
      // A manual pass pick retires the as-of request: the analyst overrode the resolution.
      return revealPass({ ...state, passDate: action.passDate, requestedDate: null });
    case "setRequestedDate":
      return { ...state, requestedDate: action.requestedDate };
    case "applyResolvedPass":
      // An as-of resolution landing on the map keeps the request so the label can state
      // "requested X -> showing Y (gap)". Reveal it too, so snapping a no-pass date to its nearest
      // stored pass actually shows that pass instead of a blank map.
      return revealPass({ ...state, passDate: action.passDate });
    case "setCompareDate":
      return { ...state, compareDate: action.compareDate };
    case "restoreView":
      // Atomic restore: setting farm then field separately would trip selectFarm's field-clear.
      return {
        ...state,
        farmId: action.view.farmId,
        fieldId: action.view.fieldId,
        index: action.view.index,
        passDate: null,
        requestedDate: null,
        compareDate: null,
      };
    case "setMapView":
      return { ...state, mapView: action.view };
    case "setCustomAOI":
      return { ...state, customAOI: action.geometry };
    case "toggleNaturalRegions":
      return { ...state, showNaturalRegions: !state.showNaturalRegions };
    case "setUploadedRegionLayer":
      return { ...state, uploadedRegionLayerId: action.layerId };
    default:
      return state;
  }
}

interface WorkspaceContextValue extends WorkspaceState {
  selectFarm: (id: string) => void;
  selectField: (id: string) => void;
  setIndex: (index: IndexKey) => void;
  setPassDate: (date: string | null) => void;
  setRequestedDate: (date: string | null) => void;
  applyResolvedPass: (passDate: string) => void;
  setCompareDate: (date: string | null) => void;
  restoreView: (view: RestoreView) => void;
  setMapView: (view: MapView) => void;
  setCustomAOI: (geometry: Geometry | null) => void;
  toggleNaturalRegions: () => void;
  setUploadedRegionLayer: (layerId: string | null) => void;
}

const WorkspaceContext = createContext<WorkspaceContextValue | null>(null);

export function WorkspaceProvider({ children }: { children: ReactNode }) {
  const [state, dispatch] = useReducer(reducer, initialState);
  const value = useMemo<WorkspaceContextValue>(
    () => ({
      ...state,
      selectFarm: (farmId) => dispatch({ type: "selectFarm", farmId }),
      selectField: (fieldId) => dispatch({ type: "selectField", fieldId }),
      setIndex: (index) => dispatch({ type: "setIndex", index }),
      setPassDate: (passDate) => dispatch({ type: "setPassDate", passDate }),
      setRequestedDate: (requestedDate) => dispatch({ type: "setRequestedDate", requestedDate }),
      applyResolvedPass: (passDate) => dispatch({ type: "applyResolvedPass", passDate }),
      setCompareDate: (compareDate) => dispatch({ type: "setCompareDate", compareDate }),
      restoreView: (view) => dispatch({ type: "restoreView", view }),
      setMapView: (view) => dispatch({ type: "setMapView", view }),
      setCustomAOI: (geometry) => dispatch({ type: "setCustomAOI", geometry }),
      toggleNaturalRegions: () => dispatch({ type: "toggleNaturalRegions" }),
      setUploadedRegionLayer: (layerId) =>
        dispatch({ type: "setUploadedRegionLayer", layerId }),
    }),
    [state],
  );
  return <WorkspaceContext.Provider value={value}>{children}</WorkspaceContext.Provider>;
}

export function useWorkspace(): WorkspaceContextValue {
  const value = useContext(WorkspaceContext);
  if (!value) throw new Error("useWorkspace must be used within a WorkspaceProvider");
  return value;
}
