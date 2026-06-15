import { useState } from "react";

import { TokenGate } from "@/auth/TokenGate";
import { useToken } from "@/auth/TokenProvider";
import { FarmFieldSidebar } from "@/components/FarmFieldSidebar";
import { FieldInspector } from "@/components/FieldInspector";
import { Header } from "@/components/Header";
import { MapPanel } from "@/components/MapPanel";
import { cn } from "@/lib/format";

export function WorkspacePage() {
  const { token } = useToken();
  const [sidebarOpen, setSidebarOpen] = useState(true);
  const [inspectorOpen, setInspectorOpen] = useState(true);

  return (
    <div className="grid h-[100dvh] max-h-[100dvh] grid-rows-[auto_minmax(0,1fr)] overflow-hidden bg-bg text-fg">
      <Header />
      {token ? (
        <main className="grid min-h-0 h-full w-full grid-cols-1 grid-rows-[auto_1fr_auto] lg:grid-cols-[auto_minmax(0,1fr)_auto] lg:grid-rows-[minmax(0,1fr)]">
          <div
            className={cn(
              "flex min-h-0 flex-col overflow-hidden border-b border-border transition-[max-width,max-height] duration-200 ease-out lg:border-b-0 lg:border-r",
              sidebarOpen
                ? "max-h-[30vh] lg:h-full lg:max-h-none lg:max-w-[300px] lg:min-w-[232px]"
                : "max-h-0 lg:h-full lg:max-h-none lg:max-w-0 lg:min-w-0",
            )}
          >
            <FarmFieldSidebar />
          </div>
          <MapPanel
            sidebarOpen={sidebarOpen}
            onToggleSidebar={() => setSidebarOpen((o) => !o)}
            inspectorOpen={inspectorOpen}
            onToggleInspector={() => setInspectorOpen((o) => !o)}
          />
          <div
            className={cn(
              "flex min-h-0 flex-col overflow-hidden border-t border-border transition-[max-width,max-height] duration-200 ease-out lg:border-l lg:border-t-0",
              inspectorOpen
                ? "max-h-[40vh] lg:h-full lg:max-h-none lg:max-w-[400px] lg:min-w-[320px]"
                : "max-h-0 lg:h-full lg:max-h-none lg:max-w-0 lg:min-w-0",
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
