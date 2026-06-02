import { useState } from "react";
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";

import { TokenGate } from "@/auth/TokenGate";
import { TokenProvider, useToken } from "@/auth/TokenProvider";
import { FarmFieldSidebar } from "@/components/FarmFieldSidebar";
import { FieldInspector } from "@/components/FieldInspector";
import { Header } from "@/components/Header";
import { MapPanel } from "@/components/MapPanel";
import { cn } from "@/lib/format";
import { WorkspaceProvider } from "@/state/workspace";

const queryClient = new QueryClient({
  defaultOptions: {
    queries: { retry: false, staleTime: 60_000, refetchOnWindowFocus: false },
  },
});

export function App() {
  return (
    <QueryClientProvider client={queryClient}>
      <TokenProvider>
        <WorkspaceProvider>
          <Shell />
        </WorkspaceProvider>
      </TokenProvider>
    </QueryClientProvider>
  );
}

function Shell() {
  const { token } = useToken();
  const [sidebarOpen, setSidebarOpen] = useState(true);
  const [inspectorOpen, setInspectorOpen] = useState(true);

  return (
    <div className="grid min-h-[100dvh] grid-rows-[auto_minmax(0,1fr)] bg-bg text-fg">
      <Header />
      {token ? (
        // Three-column layout on desktop; panels slide in/out via max-width transitions.
        // The map column is always present and fills the remaining space.
        <main className="grid min-h-0 grid-cols-1 lg:grid-cols-[auto_minmax(0,1fr)_auto]">
          {/* Sidebar — collapses horizontally on desktop, vertically on mobile */}
          <div
            className={cn(
              "min-h-0 overflow-hidden transition-[max-width,max-height] duration-200 ease-out",
              "border-b border-border lg:border-b-0 lg:border-r",
              sidebarOpen
                ? "max-h-[50vh] lg:max-h-none lg:max-w-[300px] lg:min-w-[232px]"
                : "max-h-0 lg:max-h-none lg:max-w-0 lg:min-w-0",
            )}
          >
            <FarmFieldSidebar />
          </div>

          {/* Map — always fills the center */}
          <MapPanel
            sidebarOpen={sidebarOpen}
            onToggleSidebar={() => setSidebarOpen((o) => !o)}
            inspectorOpen={inspectorOpen}
            onToggleInspector={() => setInspectorOpen((o) => !o)}
          />

          {/* Inspector — same collapse pattern as sidebar */}
          <div
            className={cn(
              "min-h-0 overflow-hidden transition-[max-width,max-height] duration-200 ease-out",
              "border-t border-border lg:border-t-0 lg:border-l",
              inspectorOpen
                ? "max-h-[50vh] lg:max-h-none lg:max-w-[400px] lg:min-w-[320px]"
                : "max-h-0 lg:max-h-none lg:max-w-0 lg:min-w-0",
            )}
          >
            <FieldInspector />
          </div>
        </main>
      ) : (
        <TokenGate />
      )}
    </div>
  );
}
