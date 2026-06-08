import { Trash } from "@phosphor-icons/react";
import { useState } from "react";
import { motion, AnimatePresence } from "motion/react";

import { ApiError, type Annotation } from "@/lib/api";
import { formatDate } from "@/lib/format";
import { useAddAnnotation, useAnnotations, useDeleteAnnotation, useFields } from "@/lib/queries";
import { useWorkspace } from "@/state/workspace";

import { EmptyState, ErrorState, LoadingRows } from "./states";
import { Badge, Button, IconButton } from "./ui";

function errorText(error: unknown, fallback: string): string {
  return error instanceof ApiError ? error.message : fallback;
}

export function AnnotationsPanel({ fieldId }: { fieldId: string }) {
  const { farmId, passDate } = useWorkspace();
  const fields = useFields(farmId);
  const field = fields.data?.find((f) => f.field_id === fieldId) ?? null;
  const notes = useAnnotations(fieldId);
  const addNote = useAddAnnotation(fieldId);
  const deleteNote = useDeleteAnnotation(fieldId);
  const [body, setBody] = useState("");

  const submit = () => {
    const trimmed = body.trim();
    if (!trimmed || addNote.isPending) return;
    // The geometry version is resolved server-side from the field (invariant 5), so the panel
    // only needs the note text and the optional pass it is pinned to.
    addNote.mutate({ body: trimmed, pass_date: passDate }, { onSuccess: () => setBody("") });
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
          disabled={addNote.isPending}
          placeholder="Observation, follow-up, or context for this field."
          className="w-full resize-y rounded-md border border-border bg-panel px-2 py-1.5 text-sm text-fg placeholder:text-muted focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-accent disabled:opacity-50"
        />
        <div className="flex items-center justify-between gap-2">
          <span className="text-[11px] text-muted">
            {passDate ? `Pinned to ${formatDate(passDate)}` : "Whole field"}
            {field ? ` · geometry v${field.geometry_version}` : ""}
          </span>
          <Button type="submit" variant="primary" disabled={!body.trim() || addNote.isPending}>
            {addNote.isPending ? "Saving..." : "Save note"}
          </Button>
        </div>
        {addNote.isError && (
          <p className="text-xs text-critical">
            {errorText(addNote.error, "Could not save the note. Try again.")}
          </p>
        )}
      </form>

      {notes.isLoading ? (
        <LoadingRows rows={3} />
      ) : notes.isError ? (
        <ErrorState error={notes.error} onRetry={() => void notes.refetch()} />
      ) : notes.data && notes.data.length ? (
        <ul className="divide-y divide-border">
          <AnimatePresence initial={false}>
            {notes.data.map((note) => (
              <NoteRow
                key={note.id}
                note={note}
                deleting={deleteNote.isPending && deleteNote.variables === note.id}
                onDelete={() => deleteNote.mutate(note.id)}
              />
            ))}
          </AnimatePresence>
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
    <motion.li
      initial={{ opacity: 0, height: 0 }}
      animate={{ opacity: 1, height: "auto" }}
      exit={{ opacity: 0, height: 0 }}
      transition={{ duration: 0.15, ease: "easeOut" }}
      className="group flex gap-2 p-3 overflow-hidden"
    >
      <div className="min-w-0 flex-1 space-y-1.5">
        <div className="flex flex-wrap items-center gap-1.5">
          {note.pass_date ? (
            <Badge tone="accent">{formatDate(note.pass_date)}</Badge>
          ) : (
            <Badge tone="neutral">whole field</Badge>
          )}
          {note.author ? <span className="text-[10px] font-medium text-fg">{note.author}</span> : null}
          <span className="text-[10px] text-muted tnum">
            {new Date(note.created_at).toLocaleString()}
          </span>
        </div>
        <p className="whitespace-pre-wrap text-sm leading-relaxed text-fg">{note.body}</p>
      </div>
      <IconButton
        label="Delete note"
        onClick={onDelete}
        disabled={deleting}
        className="shrink-0 opacity-0 transition-opacity group-hover:opacity-100 focus-visible:opacity-100"
      >
        <Trash size={14} />
      </IconButton>
    </motion.li>
  );
}
