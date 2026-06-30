import { CheckCircle } from "@phosphor-icons/react";
import { useState, type FormEvent } from "react";

import { Button } from "@/components/ui";
import { ApiError, type VisitPlot } from "@/lib/api";
import { ACTIONS, CAUSES, CONDITIONS } from "@/lib/diagnosisVocab";
import { useRecordDiagnosis } from "@/lib/queries";

const fieldClass =
  "w-full rounded-md border border-border bg-panel px-3 py-2 text-sm text-fg focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-accent focus-visible:ring-offset-1 focus-visible:ring-offset-bg";

/** The officer field-diagnosis capture form (backlog 0038). Controlled vocabularies keep every
 *  label ML-usable; the observed crop defaults to the selected plot's declared crop, and the
 *  server validates (a bad value surfaces inline as a 422). `householdId` is the visit-route id so
 *  the success invalidates the right visit package. */
export function DiagnosisForm({
  householdId,
  plots,
}: {
  householdId: string;
  plots: VisitPlot[];
}) {
  const record = useRecordDiagnosis(householdId);
  const [plotId, setPlotId] = useState(plots[0]?.plot_id ?? "");
  const [observedCrop, setObservedCrop] = useState(plots[0]?.dominant_crop ?? "");
  const [condition, setCondition] = useState("");
  const [cause, setCause] = useState("");
  const [action, setAction] = useState("");
  const [notes, setNotes] = useState("");

  const selectedPlot = plots.find((p) => p.plot_id === plotId);

  // Switching plots re-defaults the observed crop to that plot's declared crop.
  const onPlotChange = (id: string) => {
    setPlotId(id);
    setObservedCrop(plots.find((p) => p.plot_id === id)?.dominant_crop ?? "");
  };

  const canSubmit =
    !!plotId && !!observedCrop.trim() && !!condition && !!cause && !record.isPending;

  const submit = (e: FormEvent) => {
    e.preventDefault();
    if (!canSubmit) return;
    record.mutate(
      {
        plot_id: plotId,
        observed_crop: observedCrop.trim(),
        condition,
        cause,
        recommended_action: action || null,
        notes: notes.trim() || null,
        scene_id: selectedPlot?.latest_scene_id ?? null,
      },
      {
        onSuccess: () => {
          setCondition("");
          setCause("");
          setAction("");
          setNotes("");
        },
      },
    );
  };

  if (plots.length === 0) {
    return (
      <p className="text-sm text-muted">
        No plots to diagnose - enrol a plot for this household first.
      </p>
    );
  }

  return (
    <form onSubmit={submit} className="space-y-3">
      <div className="grid grid-cols-1 gap-3 sm:grid-cols-2">
        <label className="space-y-1">
          <span className="text-xs font-medium text-muted">Plot</span>
          <select
            className={fieldClass}
            value={plotId}
            onChange={(e) => onPlotChange(e.target.value)}
          >
            {plots.map((p) => (
              <option key={p.plot_id} value={p.plot_id}>
                {p.dominant_crop ? `${p.dominant_crop} · ` : ""}
                {p.plot_id.slice(0, 8)}
              </option>
            ))}
          </select>
        </label>
        <label className="space-y-1">
          <span className="text-xs font-medium text-muted">Observed crop</span>
          <input
            className={fieldClass}
            value={observedCrop}
            onChange={(e) => setObservedCrop(e.target.value)}
            placeholder="maize"
          />
        </label>
        <label className="space-y-1">
          <span className="text-xs font-medium text-muted">Condition</span>
          <select
            className={fieldClass}
            value={condition}
            onChange={(e) => setCondition(e.target.value)}
          >
            <option value="">Select...</option>
            {CONDITIONS.map((o) => (
              <option key={o.value} value={o.value}>
                {o.label}
              </option>
            ))}
          </select>
        </label>
        <label className="space-y-1">
          <span className="text-xs font-medium text-muted">Cause</span>
          <select className={fieldClass} value={cause} onChange={(e) => setCause(e.target.value)}>
            <option value="">Select...</option>
            {CAUSES.map((o) => (
              <option key={o.value} value={o.value}>
                {o.label}
              </option>
            ))}
          </select>
        </label>
        <label className="space-y-1 sm:col-span-2">
          <span className="text-xs font-medium text-muted">Recommended action (optional)</span>
          <select className={fieldClass} value={action} onChange={(e) => setAction(e.target.value)}>
            <option value="">None</option>
            {ACTIONS.map((o) => (
              <option key={o.value} value={o.value}>
                {o.label}
              </option>
            ))}
          </select>
        </label>
        <label className="space-y-1 sm:col-span-2">
          <span className="text-xs font-medium text-muted">Notes (optional)</span>
          <textarea
            className={fieldClass}
            rows={2}
            value={notes}
            onChange={(e) => setNotes(e.target.value)}
            placeholder="Free-text observations"
          />
        </label>
      </div>

      {record.isError ? (
        <p className="text-sm text-critical">
          {record.error instanceof ApiError
            ? record.error.message
            : "Could not record the diagnosis."}
        </p>
      ) : null}
      {record.isSuccess ? (
        <p className="flex items-center gap-1.5 text-sm text-positive">
          <CheckCircle size={16} weight="fill" /> Diagnosis recorded.
        </p>
      ) : null}

      <div className="flex justify-end">
        <Button type="submit" variant="primary" disabled={!canSubmit}>
          {record.isPending ? "Recording..." : "Record diagnosis"}
        </Button>
      </div>
    </form>
  );
}
