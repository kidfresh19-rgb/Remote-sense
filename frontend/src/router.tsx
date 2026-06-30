import { createRootRoute, createRoute, createRouter, Outlet } from "@tanstack/react-router";

import { AOIStudioPage } from "@/pages/AOIStudioPage";
import { DashboardPage } from "@/pages/DashboardPage";
import { VisitCockpitPage } from "@/pages/VisitCockpitPage";
import { WardWatchPage } from "@/pages/WardWatchPage";
import { WorkspacePage } from "@/pages/WorkspacePage";
import { WorkspaceProvider } from "@/state/workspace";

const rootRoute = createRootRoute({ component: () => <Outlet /> });

const dashboardRoute = createRoute({
  getParentRoute: () => rootRoute,
  path: "/",
  component: DashboardPage,
});

const workspaceRoute = createRoute({
  getParentRoute: () => rootRoute,
  path: "/workspace",
  component: () => (
    <WorkspaceProvider>
      <WorkspacePage />
    </WorkspaceProvider>
  ),
});

// AOI Studio is standalone: it manages its own AOI/index/job state locally and reuses the
// prop-driven map hook, so it needs no WorkspaceProvider.
interface AOIStudioSearch {
  /** Optional deep-link target: pre-select this canonical farm as the analysis target. Set by the
   *  dashboard farm cards' "Analyse dates" launcher; absent for a plain visit. */
  farm?: string;
}

const aoiStudioRoute = createRoute({
  getParentRoute: () => rootRoute,
  path: "/aoi-studio",
  validateSearch: (search: Record<string, unknown>): AOIStudioSearch =>
    typeof search.farm === "string" && search.farm ? { farm: search.farm } : {},
  component: AOIStudioPage,
});

const wardWatchRoute = createRoute({
  getParentRoute: () => rootRoute,
  path: "/ward-watch",
  component: WardWatchPage,
});

const wardWatchVisitRoute = createRoute({
  getParentRoute: () => rootRoute,
  path: "/ward-watch/visit/$householdId",
  component: VisitCockpitPage,
});

const routeTree = rootRoute.addChildren([
  dashboardRoute,
  workspaceRoute,
  aoiStudioRoute,
  wardWatchRoute,
  wardWatchVisitRoute,
]);

export const router = createRouter({ routeTree });

declare module "@tanstack/react-router" {
  interface Register {
    router: typeof router;
  }
}
