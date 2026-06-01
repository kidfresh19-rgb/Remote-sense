import { Info, Trash } from "@phosphor-icons/react";
import { useState } from "react";

import { addAnnotation, removeAnnotation, useAnnotations, type Annotation } from "@/lib/annotations";
import { formatDate } from "@/lib/format";
import { useFields } from "@/lib/queries";
import { useWorkspace } from "@/state/workspace";

import { EmptyState } from "./states";
import { Badge, Button, IconButton } from "./ui";

export function AnnotationsPanel({ fieldId }: { fieldId: string }) {
  const { farmId, passDate } = useWorkspace();
  const fields = useFields(farmId);
  const field = fields.data?.find((f) => f.field_id === fieldId) ?? null;
  const notes = useAnnotations(fieldId);
  const [body, setBody] = useState("");

  const submit = () => {
    const trimmed = body.trim();
    // Require the resolved field so the note is pinned to the real geometry version (invariant 5),
    // never a guessed default while the field list is still loading.
    if (!trimmed || !field) return;
    addAnnotation({
      fieldId,
      geometryVersion: field.geometry_version,
      passDate,
      body: trimmed,
      author: null,
    });
    setBody("");
  };

  return (
    <div className="flex flex-col">
      <form
        className="space-y-2 border-b border-border p-3"
        onSubmit={(e) => {
          e.preventDefault();
          submit();
        }}
      >
        <label htmlFor="note-body" className="block text-xs font-medium text-fg">
          Add a note
        </label>
        <textarea
          id="note-body"
          value={body}
          onChange={(e) => setBody(e.target.value)}
          onKeyDown={(e) => {
            if ((e.metaKey || e.ctrlKey) && e.key === "Enter") submit();
          }}
          rows={3}
          placeholder="Observation, follow-up, or context for this field."
          className="w-full resize-y rounded-md border border-border bg-panel px-2 py-1.5 text-sm text-fg placeholder:text-muted focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-accent"
        />
        <div className="flex items-center justify-between gap-2">
          <span className="text-[11px] text-muted">
            {passDate ? `Pinned to ${formatDate(passDate)}` : "Whole field"} · geometry v
            {field?.geometry_version ?? "?"}
          </span>
          <Button type="submit" variant="primary" disabled={!body.trim() || !field}>
            Save note
          </Button>
        </div>
      </form>

      <p className="flex items-start gap-1.5 px-3 py-2 text-[11px] leading-relaxed text-muted">
        <Info size={13} className="mt-0.5 shrink-0" />
        Notes are saved to this browser only, pending the shared annotation store.
      </p>

      {notes.length ? (
        <ul className="divide-y divide-border">
          {notes.map((note) => (
            <NoteRow key={note.id} note={note} />
          ))}
        </ul>
      ) : (
        <EmptyState title="No notes yet" hint="Pin observations to this field as you work." />
      )}
    </div>
  );
}

function NoteRow({ note }: { note: Annotation }) {
  return (
    <li className="group flex gap-2 p-3">
      <div className="min-w-0 flex-1 space-y-1.5">
        <div className="flex flex-wrap items-center gap-1.5">
          {note.passDate ? (
            <Badge tone="accent">{formatDate(note.passDate)}</Badge>
          ) : (
            <Badge tone="neutral">whole field</Badge>
          )}
          <span className="text-[10px] text-muted tnum">
            {new Date(note.createdAt).toLocaleString()}
          </span>
        </div>
        <p className="whitespace-pre-wrap text-sm leading-relaxed text-fg">{note.body}</p>
      </div>
      <IconButton
        label="Delete note"
        onClick={() => removeAnnotation(note.id)}
        className="shrink-0 opacity-0 transition-opacity group-hover:opacity-100 focus-visible:opacity-100"
      >
        <Trash size={14} />
      </IconButton>
    </li>
  );
}
