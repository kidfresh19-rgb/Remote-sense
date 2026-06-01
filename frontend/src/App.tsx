import { QueryClient, QueryClientProvider } from "@tanstack/react-query";

import { TokenGate } from "@/auth/TokenGate";
import { TokenProvider, useToken } from "@/auth/TokenProvider";
import { FarmFieldSidebar } from "@/components/FarmFieldSidebar";
import { FieldInspector } from "@/components/FieldInspector";
import { Header } from "@/components/Header";
import { MapPanel } from "@/components/MapPanel";
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
  return (
    <div className="grid min-h-[100dvh] grid-rows-[auto_minmax(0,1fr)] bg-bg text-fg">
      <Header />
      {token ? (
        // Three columns on desktop (fields / map / inspector). Below lg the panels stack: the map
        // keeps a viewport-relative min height so it stays usable, the side panels flow above/below.
        <main className="grid min-h-0 grid-cols-1 lg:grid-cols-[clamp(232px,18vw,300px)_minmax(0,1fr)_clamp(320px,26vw,400px)]">
          <FarmFieldSidebar />
          <MapPanel />
          <FieldInspector />
        </main>
      ) : (
        <TokenGate />
      )}
    </div>
  );
}
