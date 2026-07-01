import type { Geometry } from "geojson";
import { createContext, useContext, useMemo, useReducer, type ReactNode } from "react";

import { DEFAULT_INDEX, type IndexKey } from "@/lib/indices";

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
  // Small-multiples grid of every pass's thumbnail (backlog 0044). Mutually exclusive with
  // compare mode - both replace the single map, so the reducer keeps them from being on at once.
  showContactSheet: boolean;
  showRaster: boolean;
  showRgb: boolean;
  showFcc: boolean; // false color (NIR/red/green): vegetation renders red
  customAOI: Geometry | null; // user-defined analysis boundary
  // Region-boundary map overlays (comparison groups, PRD 0002 slices 8a/8b). Global map context,
  // independent of the selected field, so they are not cleared on a farm/field change.
  showNaturalRegions: boolean; // the seeded Natural Region layer
  uploadedRegionLayerId: string | null; // the chosen analyst-uploaded layer, or none
  // Cloud-mask honesty overlay (backlog 0046): a semi-transparent hatch over SCL-masked pixels for
  // the active pass. Independent of showRaster/showRgb/showFcc - it renders on top of whichever
  // base layer is showing, so it is never coupled to (or cleared by) that selection. Like
  // showNaturalRegions, this is map context, not field-scoped state, so it survives farm/field
  // changes and restoreView.
  showCloudMask: boolean;
  // Which of SceneCompare's two views is showing (backlog 0045): the existing synced side-by-side
  // panes, or the pass-to-pass difference layer. Only meaningful while compareDate is set, and
  // reset to "side-by-side" whenever compare mode is exited (or the field/farm changes) so a stale
  // "diff" selection never carries into a fresh, not-yet-explicit A/B pairing.
  compareMode: "side-by-side" | "diff";
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
  | { type: "toggleContactSheet" }
  | { type: "toggleRaster" }
  | { type: "toggleRgb" }
  | { type: "toggleFcc" }
  | { type: "setCustomAOI"; geometry: Geometry | null }
  | { type: "toggleNaturalRegions" }
  | { type: "setUploadedRegionLayer"; layerId: string | null }
  | { type: "toggleCloudMask" }
  | { type: "setCompareMode"; compareMode: "side-by-side" | "diff" };

const initialState: WorkspaceState = {
  farmId: null,
  fieldId: null,
  index: DEFAULT_INDEX,
  passDate: null,
  requestedDate: null,
  compareDate: null,
  showContactSheet: false,
  showRaster: false,
  showRgb: false,
  showFcc: false,
  customAOI: null,
  showNaturalRegions: false,
  uploadedRegionLayerId: null,
  showCloudMask: false,
  compareMode: "side-by-side",
};

function reducer(state: WorkspaceState, action: Action): WorkspaceState {
  switch (action.type) {
    case "selectFarm":
      if (action.farmId === state.farmId) return state;
      // Switching farm clears the field-scoped selection so panels never show stale context.
      return {
        ...state,
        farmId: action.farmId,
        fieldId: null,
        passDate: null,
        requestedDate: null,
        compareDate: null,
        showContactSheet: false,
        showRgb: false,
        showFcc: false,
        compareMode: "side-by-side",
      };
    case "selectField":
      if (action.fieldId === state.fieldId) return state;
      return {
        ...state,
        fieldId: action.fieldId,
        passDate: null,
        requestedDate: null,
        compareDate: null,
        showContactSheet: false,
        showRgb: false,
        showFcc: false,
        compareMode: "side-by-side",
      };
    case "setIndex":
      return { ...state, index: action.index };
    case "setPassDate":
      // A manual pass pick retires the as-of request: the analyst overrode the resolution.
      return { ...state, passDate: action.passDate, requestedDate: null };
    case "setRequestedDate":
      return { ...state, requestedDate: action.requestedDate };
    case "applyResolvedPass":
      // An as-of resolution landing on the map keeps the request so the label can state
      // "requested X -> showing Y (gap)".
      return { ...state, passDate: action.passDate };
    case "setCompareDate":
      // Entering (or switching) compare replaces the contact sheet, mirroring the reverse case in
      // toggleContactSheet - the two grid/pane views can never both be on. Leaving compare entirely
      // (compareDate -> null) also drops back to side-by-side, so a stale "diff" selection never
      // carries into the next time compare is entered.
      return {
        ...state,
        compareDate: action.compareDate,
        showContactSheet: action.compareDate !== null ? false : state.showContactSheet,
        compareMode: action.compareDate === null ? "side-by-side" : state.compareMode,
      };
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
        showContactSheet: false,
        showRgb: false,
        showFcc: false,
        compareMode: "side-by-side",
      };
    case "toggleContactSheet":
      const nextShowContactSheet = !state.showContactSheet;
      // Entering the contact sheet replaces compare mode for the same reason.
      return {
        ...state,
        showContactSheet: nextShowContactSheet,
        compareDate: nextShowContactSheet ? null : state.compareDate,
      };
    case "toggleRaster":
      const nextShowRaster = !state.showRaster;
      return {
        ...state,
        showRaster: nextShowRaster,
        showRgb: nextShowRaster ? false : state.showRgb,
        showFcc: nextShowRaster ? false : state.showFcc,
      };
    case "toggleRgb":
      const nextShowRgb = !state.showRgb;
      return {
        ...state,
        showRgb: nextShowRgb,
        showRaster: nextShowRgb ? false : state.showRaster,
        showFcc: nextShowRgb ? false : state.showFcc,
      };
    case "toggleFcc":
      const nextShowFcc = !state.showFcc;
      return {
        ...state,
        showFcc: nextShowFcc,
        showRaster: nextShowFcc ? false : state.showRaster,
        showRgb: nextShowFcc ? false : state.showRgb,
      };
    case "setCustomAOI":
      return { ...state, customAOI: action.geometry };
    case "toggleNaturalRegions":
      return { ...state, showNaturalRegions: !state.showNaturalRegions };
    case "setUploadedRegionLayer":
      return { ...state, uploadedRegionLayerId: action.layerId };
    case "toggleCloudMask":
      return { ...state, showCloudMask: !state.showCloudMask };
    case "setCompareMode":
      return { ...state, compareMode: action.compareMode };
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
  toggleContactSheet: () => void;
  toggleRaster: () => void;
  toggleRgb: () => void;
  toggleFcc: () => void;
  setCustomAOI: (geometry: Geometry | null) => void;
  toggleNaturalRegions: () => void;
  setUploadedRegionLayer: (layerId: string | null) => void;
  toggleCloudMask: () => void;
  setCompareMode: (mode: "side-by-side" | "diff") => void;
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
      toggleContactSheet: () => dispatch({ type: "toggleContactSheet" }),
      toggleRaster: () => dispatch({ type: "toggleRaster" }),
      toggleRgb: () => dispatch({ type: "toggleRgb" }),
      toggleFcc: () => dispatch({ type: "toggleFcc" }),
      setCustomAOI: (geometry) => dispatch({ type: "setCustomAOI", geometry }),
      toggleNaturalRegions: () => dispatch({ type: "toggleNaturalRegions" }),
      setUploadedRegionLayer: (layerId) =>
        dispatch({ type: "setUploadedRegionLayer", layerId }),
      toggleCloudMask: () => dispatch({ type: "toggleCloudMask" }),
      setCompareMode: (mode) => dispatch({ type: "setCompareMode", compareMode: mode }),
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
