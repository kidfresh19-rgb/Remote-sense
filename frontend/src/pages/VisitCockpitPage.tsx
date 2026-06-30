import { FirstAid, ListChecks, MapPin, Plant, Question, Warning } from "@phosphor-icons/react";
import { useParams } from "@tanstack/react-router";
import type { ReactNode } from "react";

import { useCanRecordDiagnosis, useCanViewTriage } from "@/auth/permissions";
import { TokenGate } from "@/auth/TokenGate";
import { useToken } from "@/auth/TokenProvider";
import { EmptyState, ErrorState, LoadingRows } from "@/components/states";
import { Badge } from "@/components/ui";
import { DiagnosisForm } from "@/components/wardwatch/DiagnosisForm";
import { PlotTrend } from "@/components/wardwatch/PlotTrend";
import { WardWatchTopBar } from "@/components/wardwatch/WardWatchTopBar";
import type { AlertHint, PreviousVisit, VisitAssessment, VisitPlot } from "@/lib/api";
import { formatDate, formatNumber } from "@/lib/format";
import { useWardWatchVisit } from "@/lib/queries";
import { cohortLevelLabel, movementMeta } from "@/lib/wardwatch";

const humanize = (s: string) => s.replace(/_/g, " ").replace(/^\w/, (c) => c.toUpperCase());

function Section({
  icon,
  title,
  children,
}: {
  icon: ReactNode;
  title: string;
  children: ReactNode;
}) {
  return (
    <section className="overflow-hidden rounded-xl border border-border bg-panel">
      <div className="flex items-center gap-2 border-b border-border px-4 py-3">
        <span className="text-muted">{icon}</span>
        <h2 className="text-sm font-semibold text-fg">{title}</h2>
      </div>
      <div className="p-4">{children}</div>
    </section>
  );
}

function AssessmentCard({ assessment }: { assessment: VisitAssessment }) {
  const meta = movementMeta(assessment.label);
  const flags = [
    !assessment.cohort_meets_quorum ? "Small cohort (below quorum)" : null,
    assessment.low_pixel_quality ? "Low pixel quality" : null,
  ].filter(Boolean) as string[];
  return (
    <Section icon={<Plant size={18} />} title="Movement assessment">
      <div className="flex flex-wrap items-center gap-3">
        <Badge tone={meta.tone}>{meta.label}</Badge>
        <span className="tnum text-sm font-semibold text-fg">
          {formatNumber(assessment.robust_deviation, 2)}
          <span className="ml-1 text-xs font-normal text-muted">robust deviation</span>
        </span>
        <span className="text-xs text-muted">·</span>
        <span className="text-xs text-muted">{cohortLevelLabel(assessment.cohort_level)}</span>
      </div>
      <p className="mt-2 text-sm text-muted">{meta.blurb}</p>
      {flags.length > 0 ? (
        <div className="mt-3 flex flex-wrap gap-2">
          {flags.map((f) => (
            <span
              key={f}
              className="inline-flex items-center gap-1 rounded-md bg-caution/10 px-2 py-1 text-xs text-caution"
            >
              <Warning size={13} weight="fill" />
              {f}
            </span>
          ))}
        </div>
      ) : null}
    </Section>
  );
}

function AlertHintsCard({ hints }: { hints: AlertHint[] }) {
  return (
    <Section icon={<Warning size={18} />} title="Alert hints">
      <p className="mb-3 text-xs italic text-muted">{hints[0].framing}</p>
      <ul className="space-y-3">
        {hints.map((hint) => (
          <li key={hint.category} className="rounded-lg border border-border bg-panel-2/40 p-3">
            <div className="flex items-center justify-between gap-2">
              <p className="text-sm font-medium text-fg">{hint.headline}</p>
              <Badge tone="caution">{humanize(hint.category)}</Badge>
            </div>
            <p className="mt-1 text-xs text-muted">{hint.signature}</p>
            <div className="mt-2 h-1.5 overflow-hidden rounded-full bg-panel-2">
              <div
                className="h-full bg-caution"
                style={{ width: `${Math.round(Math.min(1, Math.max(0, hint.strength)) * 100)}%` }}
              />
            </div>
          </li>
        ))}
      </ul>
    </Section>
  );
}

function QuestionsCard({ questions }: { questions: string[] }) {
  return (
    <Section icon={<Question size={18} />} title="Questions for the visit">
      <ul className="space-y-2">
        {questions.map((q) => (
          <li key={q} className="flex items-start gap-2 text-sm text-fg">
            <ListChecks size={16} className="mt-0.5 shrink-0 text-muted" />
            <span>{q}</span>
          </li>
        ))}
      </ul>
    </Section>
  );
}

function PlotCard({ plot }: { plot: VisitPlot }) {
  const meta = [
    plot.dominant_crop,
    plot.planting_window ? `${humanize(plot.planting_window)} window` : null,
    plot.size_class ? humanize(plot.size_class) : null,
    plot.area_m2 ? `${(plot.area_m2 / 10000).toFixed(2)} ha` : null,
  ].filter(Boolean);
  return (
    <div className="rounded-lg border border-border bg-panel-2/40 p-3">
      <div className="flex items-center justify-between gap-2">
        <p className="truncate font-mono text-xs text-muted">{plot.plot_id}</p>
        {plot.latest_pass_date ? (
          <span className="shrink-0 text-[11px] text-muted">{formatDate(plot.latest_pass_date)}</span>
        ) : null}
      </div>
      {meta.length > 0 ? <p className="mt-1 text-sm text-fg">{meta.join(" · ")}</p> : null}
      <div className="mt-3">
        <PlotTrend trend={plot.trend} />
      </div>
    </div>
  );
}

function PreviousVisitRow({ visit }: { visit: PreviousVisit }) {
  return (
    <li className="rounded-lg border border-border bg-panel-2/40 p-3">
      <div className="flex items-center justify-between gap-2">
        <Badge tone="neutral">{humanize(visit.condition)}</Badge>
        <span className="text-[11px] text-muted">{formatDate(visit.observed_on)}</span>
      </div>
      <p className="mt-2 text-xs text-muted">
        {[
          visit.observed_crop,
          `cause: ${humanize(visit.cause)}`,
          visit.recommended_action ? `action: ${humanize(visit.recommended_action)}` : null,
        ]
          .filter(Boolean)
          .join(" · ")}
      </p>
      {visit.notes ? <p className="mt-1 text-sm text-fg">{visit.notes}</p> : null}
    </li>
  );
}

export function VisitCockpitPage() {
  const { householdId } = useParams({ from: "/ward-watch/visit/$householdId" });
  const { token } = useToken();
  const canView = useCanViewTriage();
  const canRecord = useCanRecordDiagnosis();
  const visit = useWardWatchVisit(token && canView ? householdId : null);

  return (
    <div className="flex min-h-[100dvh] flex-col bg-bg text-fg">
      <WardWatchTopBar
        title={householdId}
        subtitle="physical visit package"
        backTo="/ward-watch"
        backLabel="Back to triage queue"
      />
      {!token ? (
        <TokenGate />
      ) : !canView ? (
        <main className="flex flex-1 items-center justify-center">
          <EmptyState
            title="No access to this household"
            hint="Viewing a household's visit package needs the triage-queue permission (ward officer or district agronomist)."
          />
        </main>
      ) : (
        <main className="flex-1 overflow-y-auto">
          <div className="mx-auto max-w-[920px] px-6 py-6 pb-12">
            {visit.isPending ? (
              <LoadingRows rows={8} />
            ) : visit.isError ? (
              <ErrorState error={visit.error} onRetry={() => visit.refetch()} />
            ) : !visit.data ? (
              <EmptyState
                icon={<MapPin size={22} />}
                title="Household not found"
                hint="No household with this id is held. It may not have synced from the gateway yet."
              />
            ) : (
              <div className="space-y-5">
                <div className="flex flex-wrap items-center gap-2">
                  {visit.data.ward ? <Badge tone="accent">{visit.data.ward}</Badge> : null}
                  {visit.data.village ? <Badge tone="neutral">{visit.data.village}</Badge> : null}
                  {visit.data.dominant_nr ? (
                    <Badge tone="neutral">{visit.data.dominant_nr}</Badge>
                  ) : null}
                  {visit.data.dominant_crop ? (
                    <Badge tone="neutral">{visit.data.dominant_crop}</Badge>
                  ) : null}
                </div>

                {visit.data.assessment ? (
                  <AssessmentCard assessment={visit.data.assessment} />
                ) : (
                  <Section icon={<Plant size={18} />} title="Movement assessment">
                    <p className="text-sm text-muted">
                      No cohort assessment yet - this household has no plot with enough clear passes
                      to read a trend against its peers.
                    </p>
                  </Section>
                )}

                {visit.data.alert_hints.length > 0 ? (
                  <AlertHintsCard hints={visit.data.alert_hints} />
                ) : null}

                {visit.data.recommended_questions.length > 0 ? (
                  <QuestionsCard questions={visit.data.recommended_questions} />
                ) : null}

                <Section icon={<Plant size={18} />} title={`Plots (${visit.data.plots.length})`}>
                  {visit.data.plots.length === 0 ? (
                    <p className="text-sm text-muted">No plots enrolled for this household.</p>
                  ) : (
                    <div className="space-y-3">
                      {visit.data.plots.map((plot) => (
                        <PlotCard key={plot.plot_id} plot={plot} />
                      ))}
                    </div>
                  )}
                </Section>

                <Section icon={<ListChecks size={18} />} title="Visit history">
                  {visit.data.previous_visits.length === 0 ? (
                    <p className="text-sm text-muted">
                      No diagnoses recorded yet. The first field visit starts the history.
                    </p>
                  ) : (
                    <ul className="space-y-2">
                      {visit.data.previous_visits.map((v) => (
                        <PreviousVisitRow key={v.diagnosis_id} visit={v} />
                      ))}
                    </ul>
                  )}
                </Section>

                {canRecord ? (
                  <Section icon={<FirstAid size={18} />} title="Record a diagnosis">
                    <DiagnosisForm householdId={householdId} plots={visit.data.plots} />
                  </Section>
                ) : null}
              </div>
            )}
          </div>
        </main>
      )}
    </div>
  );
}
