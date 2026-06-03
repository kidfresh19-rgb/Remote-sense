import { Trash } from "@phosphor-icons/react";
import { useState } from "react";

import type { Annotation } from "@/lib/api";
import { formatDate } from "@/lib/format";
import { useAddAnnotation, useAnnotations, useFields, useRemoveAnnotation } from "@/lib/queries";
import { useWorkspace } from "@/state/workspace";

import { EmptyState, ErrorState, LoadingRows } from "./states";
import { Badge, Button, IconButton } from "./ui";

export function AnnotationsPanel({ fieldId }: { fieldId: string }) {
  const { farmId, passDate } = useWorkspace();
  const fields = useFields(farmId);
  const field = fields.data?.find((f) => f.field_id === fieldId) ?? null;
  const notesQuery = useAnnotations(fieldId);
  const addNote = useAddAnnotation(fieldId);
  const removeNote = useRemoveAnnotation(fieldId);
  const [body, setBody] = useState("");

  const submit = () => {
    const trimmed = body.trim();
    if (!trimmed || addNote.isPending) return;
    // The server pins the note to the field's current geometry version (invariant 5); the client
    // only sends the body and the optional pass-date scope.
    addNote.mutate({ body: trimmed, pass_date: passDate }, { onSuccess: () => setBody("") });
  };

  const notes = notesQuery.data ?? [];

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
          <Button type="submit" variant="primary" disabled={!body.trim() || addNote.isPending}>
            {addNote.isPending ? "Saving…" : "Save note"}
          </Button>
        </div>
        {addNote.isError ? (
          <p className="text-[11px] text-muted">
            Could not save the note (need the annotate permission). Try again.
          </p>
        ) : null}
      </form>

      {notesQuery.isLoading ? (
        <LoadingRows />
      ) : notesQuery.isError ? (
        <ErrorState error={notesQuery.error} onRetry={() => notesQuery.refetch()} />
      ) : notes.length ? (
        <ul className="divide-y divide-border">
          {notes.map((note) => (
            <NoteRow
              key={note.id}
              note={note}
              onDelete={() => removeNote.mutate(note.id)}
              deleting={removeNote.isPending && removeNote.variables === note.id}
            />
          ))}
        </ul>
      ) : (
        <EmptyState title="No notes yet" hint="Pin observations to this field as you work." />
      )}
    </div>
  );
}

function NoteRow({
  note,
  onDelete,
  deleting,
}: {
  note: Annotation;
  onDelete: () => void;
  deleting: boolean;
}) {
  return (
    <li className="group flex gap-2 p-3" aria-busy={deleting}>
      <div className="min-w-0 flex-1 space-y-1.5">
        <div className="flex flex-wrap items-center gap-1.5">
          {note.pass_date ? (
            <Badge tone="accent">{formatDate(note.pass_date)}</Badge>
          ) : (
            <Badge tone="neutral">whole field</Badge>
          )}
          <span className="text-[10px] text-muted tnum">
            {new Date(note.created_at).toLocaleString()}
          </span>
          {note.author ? <span className="text-[10px] text-muted">{`· ${note.author}`}</span> : null}
        </div>
        <p className="whitespace-pre-wrap text-sm leading-relaxed text-fg">{note.body}</p>
      </div>
      <IconButton
        label="Delete note"
        onClick={onDelete}
        className="shrink-0 opacity-0 transition-opacity group-hover:opacity-100 focus-visible:opacity-100"
      >
        <Trash size={14} />
      </IconButton>
    </li>
  );
}
