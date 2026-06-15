import { createRootRoute, createRoute, createRouter, Outlet } from "@tanstack/react-router";

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

const routeTree = rootRoute.addChildren([dashboardRoute, workspaceRoute]);

export const router = createRouter({ routeTree });

declare module "@tanstack/react-router" {
  interface Register {
    router: typeof router;
  }
}
