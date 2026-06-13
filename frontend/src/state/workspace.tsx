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
  showRaster: boolean;
  showRgb: boolean;
  showFcc: boolean; // false color (NIR/red/green): vegetation renders red
  customAOI: Geometry | null; // user-defined analysis boundary
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
  | { type: "toggleRaster" }
  | { type: "toggleRgb" }
  | { type: "toggleFcc" }
  | { type: "setCustomAOI"; geometry: Geometry | null };

const initialState: WorkspaceState = {
  farmId: null,
  fieldId: null,
  index: DEFAULT_INDEX,
  passDate: null,
  requestedDate: null,
  compareDate: null,
  showRaster: false,
  showRgb: false,
  showFcc: false,
  customAOI: null,
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
        showRgb: false,
        showFcc: false,
      };
    case "selectField":
      if (action.fieldId === state.fieldId) return state;
      return {
        ...state,
        fieldId: action.fieldId,
        passDate: null,
        requestedDate: null,
        compareDate: null,
        showRgb: false,
        showFcc: false,
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
        showRgb: false,
        showFcc: false,
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
  toggleRaster: () => void;
  toggleRgb: () => void;
  toggleFcc: () => void;
  setCustomAOI: (geometry: Geometry | null) => void;
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
      toggleRaster: () => dispatch({ type: "toggleRaster" }),
      toggleRgb: () => dispatch({ type: "toggleRgb" }),
      toggleFcc: () => dispatch({ type: "toggleFcc" }),
      setCustomAOI: (geometry) => dispatch({ type: "setCustomAOI", geometry }),
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
