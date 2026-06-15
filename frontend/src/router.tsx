import { createRootRoute, createRoute, createRouter, Outlet } from "@tanstack/react-router";

import { AOIStudioPage } from "@/pages/AOIStudioPage";
import { DashboardPage } from "@/pages/DashboardPage";
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
const aoiStudioRoute = createRoute({
  getParentRoute: () => rootRoute,
  path: "/aoi-studio",
  component: AOIStudioPage,
});

const routeTree = rootRoute.addChildren([dashboardRoute, workspaceRoute, aoiStudioRoute]);

export const router = createRouter({ routeTree });

declare module "@tanstack/react-router" {
  interface Register {
    router: typeof router;
  }
}
