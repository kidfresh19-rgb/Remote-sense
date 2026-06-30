import { useNavigate } from "@tanstack/react-router";

import { useCanViewRollups, useCanViewTriage } from "@/auth/permissions";
import { TokenGate } from "@/auth/TokenGate";
import { useToken } from "@/auth/TokenProvider";
import { EmptyState } from "@/components/states";
import { FoodSecurityRollups } from "@/components/wardwatch/FoodSecurityRollups";
import { TriageQueue } from "@/components/wardwatch/TriageQueue";
import { WardWatchTopBar } from "@/components/wardwatch/WardWatchTopBar";
import { cn } from "@/lib/format";

const PANEL = "h-[70dvh] min-h-[440px] overflow-hidden rounded-xl border border-border bg-panel";

export function WardWatchPage() {
  const { token } = useToken();
  const navigate = useNavigate();
  const canTriage = useCanViewTriage();
  const canRollups = useCanViewRollups();
  const hasAccess = canTriage || canRollups;

  return (
    <div className="flex min-h-[100dvh] flex-col bg-bg text-fg">
      <WardWatchTopBar
        title="Ward Watch"
        subtitle="officer triage cockpit"
        backTo="/"
        backLabel="Back to dashboard"
      />
      {!token ? (
        <TokenGate />
      ) : !hasAccess ? (
        <main className="flex flex-1 items-center justify-center">
          <EmptyState
            title="No Ward Watch access"
            hint="Your role does not include the officer queue or the food-security rollups. Ask an administrator for a ward officer, district agronomist or ministry role."
          />
        </main>
      ) : (
        <main className="flex-1 overflow-y-auto">
          <div className="mx-auto max-w-[1400px] px-6 py-6 pb-10">
            <p className="mb-4 text-xs font-semibold uppercase tracking-widest text-muted/70">
              Officer cockpit
            </p>
            <div className="grid grid-cols-1 gap-5 lg:grid-cols-3">
              {canTriage ? (
                <section className={cn(PANEL, canRollups ? "lg:col-span-2" : "lg:col-span-3")}>
                  <TriageQueue
                    onSelectHousehold={(householdId) =>
                      navigate({ to: "/ward-watch/visit/$householdId", params: { householdId } })
                    }
                  />
                </section>
              ) : null}
              {canRollups ? (
                <section className={cn(PANEL, canTriage ? "lg:col-span-1" : "lg:col-span-3")}>
                  <FoodSecurityRollups />
                </section>
              ) : null}
            </div>
          </div>
        </main>
      )}
    </div>
  );
}
