import { TokenGate } from "@/auth/TokenGate";
import { useToken } from "@/auth/TokenProvider";
import { ActivityFeed } from "@/components/dashboard/ActivityFeed";
import { AnalyticsCards } from "@/components/dashboard/AnalyticsCards";
import { DashboardHeader } from "@/components/dashboard/DashboardHeader";
import { EstateHealthCards } from "@/components/dashboard/EstateHealthCards";
import { FarmCards } from "@/components/dashboard/FarmCards";
import { FieldHealthMap } from "@/components/dashboard/FieldHealthMap";

export function DashboardPage() {
  const { token } = useToken();

  return (
    <div className="flex min-h-[100dvh] flex-col bg-bg text-fg">
      <DashboardHeader />
      {token ? (
        <main className="flex-1 overflow-y-auto">
          <div className="mx-auto max-w-[1400px] px-6 py-6 pb-10">
            <section>
              <p className="mb-4 text-xs font-semibold uppercase tracking-widest text-muted/70">
                Estate overview
              </p>
              <EstateHealthCards />
            </section>

            <section className="mt-5 grid grid-cols-1 gap-5 lg:grid-cols-3">
              <div className="h-[460px] overflow-hidden rounded-xl border border-border lg:col-span-2">
                <FieldHealthMap />
              </div>
              <div className="h-[460px] overflow-hidden rounded-xl border border-border">
                <ActivityFeed />
              </div>
            </section>

            <section className="mt-5">
              <AnalyticsCards />
            </section>

            <section className="mt-8">
              <p className="mb-4 text-xs font-semibold uppercase tracking-widest text-muted/70">
                Farms
              </p>
              <FarmCards />
            </section>
          </div>
        </main>
      ) : (
        <TokenGate />
      )}
    </div>
  );
}
