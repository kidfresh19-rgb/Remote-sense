import { ClockCounterClockwise } from "@phosphor-icons/react";

import type { AuditRecord } from "@/lib/api";
import { formatDate, formatPercent } from "@/lib/format";
import { useAudit } from "@/lib/queries";

import { EmptyState, ErrorState, LoadingRows } from "./states";
import { Badge } from "./ui";

/** Provenance log for the selected field: the tuple (CLAUDE.md invariant 5) that reproduces every
 *  stored value. Read-only; this is internal reproducibility metadata, never the gateway push. */
export function AuditPanel({ fieldId }: { fieldId: string }) {
  const query = useAudit(fieldId);

  if (query.isLoading) return <LoadingRows />;
  if (query.isError) return <ErrorState error={query.error} onRetry={() => query.refetch()} />;
  const records = query.data ?? [];
  if (!records.length) {
    return (
      <EmptyState
        icon={<ClockCounterClockwise size={32} weight="duotone" className="text-accent" />}
        title="Provenance log is empty"
        hint="When the collection pipeline processes satellite passes for this field, each computed index value and its full provenance tuple (scene, formula, geometry version, processing mode) appears here. This log is read-only and verifiable."
      />
    );
  }

  return (
    <ul className="divide-y divide-border">
      {records.map((r) => (
        <AuditRow key={`${r.scene_id}:${r.index_name}:${r.geometry_version}:${r.formula_version}`} record={r} />
      ))}
    </ul>
  );
}

function AuditRow({ record }: { record: AuditRecord }) {
  return (
    <li className="space-y-2 p-3">
      <div className="flex flex-wrap items-center gap-1.5">
        <span className="mr-1 text-xs text-muted">{formatDate(record.pass_date)}</span>
        <Badge tone="accent">{record.index_name.toUpperCase()}</Badge>
        <Badge tone="neutral">{record.processing_mode}</Badge>
      </div>
      <dl className="grid grid-cols-2 gap-x-3 gap-y-1 text-[11px]">
        <Field label="Scene" value={record.scene_id} mono />
        <Field label="Provider" value={record.provider} />
        <Field label="Formula" value={`v${record.formula_version}`} />
        <Field label="Geometry" value={`v${record.geometry_version}`} />
        <Field label="Resolution" value={`${record.resolution_m} m`} />
        <Field label="Clear" value={formatPercent(record.clear_fraction)} />
      </dl>
    </li>
  );
}

function Field({ label, value, mono }: { label: string; value: string; mono?: boolean }) {
  return (
    <div className="min-w-0">
      <dt className="text-[10px] uppercase tracking-wide text-muted">{label}</dt>
      <dd className={mono ? "truncate font-mono text-fg" : "truncate text-fg"} title={value}>
        {value}
      </dd>
    </div>
  );
}
