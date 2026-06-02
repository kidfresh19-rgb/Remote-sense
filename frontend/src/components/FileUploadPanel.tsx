import { FileArrowUp, X } from "@phosphor-icons/react";
import type { Geometry } from "geojson";
import { useRef, useState } from "react";

import { cn } from "@/lib/format";
import { parseAOIFile } from "@/lib/parseAOI";

import { Badge, Button } from "./ui";

interface FileUploadPanelProps {
  onClose: () => void;
  onApply: (geometry: Geometry, label: string) => void;
}

type ParseStatus = "idle" | "parsing" | "done" | "error";

interface ParseState {
  status: ParseStatus;
  geometry: Geometry | null;
  label: string;
  error: string;
}

const INITIAL_PARSE: ParseState = {
  status: "idle",
  geometry: null,
  label: "",
  error: "",
};

export function FileUploadPanel({ onClose, onApply }: FileUploadPanelProps) {
  const [isDragging, setIsDragging] = useState(false);
  const [parse, setParse] = useState<ParseState>(INITIAL_PARSE);
  const [fileName, setFileName] = useState<string | null>(null);
  const inputRef = useRef<HTMLInputElement | null>(null);

  async function processFile(file: File) {
    setFileName(file.name);
    setParse({ status: "parsing", geometry: null, label: "", error: "" });
    const result = await parseAOIFile(file);
    if (result.ok) {
      setParse({ status: "done", geometry: result.geometry, label: result.label, error: "" });
    } else {
      setParse({ status: "error", geometry: null, label: "", error: result.error });
    }
  }

  function handleDrop(e: React.DragEvent<HTMLDivElement>) {
    e.preventDefault();
    setIsDragging(false);
    const file = e.dataTransfer.files[0];
    if (file) void processFile(file);
  }

  function handleInputChange(e: React.ChangeEvent<HTMLInputElement>) {
    const file = e.target.files?.[0];
    if (file) void processFile(file);
    // Reset so the same file can be re-selected after an error.
    e.target.value = "";
  }

  function handleApply() {
    if (parse.status !== "done" || !parse.geometry) return;
    onApply(parse.geometry, parse.label);
    onClose();
  }

  function geometryTypeBadge(type: string) {
    const isMixed = type.startsWith("Multi");
    return (
      <Badge tone={isMixed ? "caution" : "accent"} className="uppercase">
        {type}
      </Badge>
    );
  }

  return (
    <div className="absolute inset-0 z-40 flex items-center justify-center bg-bg/60 backdrop-blur-sm">
      <div className="relative w-full max-w-md rounded-xl border border-border bg-panel shadow-xl">
        {/* Header */}
        <div className="flex items-center justify-between border-b border-border px-4 py-3">
          <h2 className="text-sm font-semibold text-fg">Upload boundary file</h2>
          <button
            onClick={onClose}
            aria-label="Close"
            className="rounded-md p-1 text-muted transition-colors hover:bg-panel-2 hover:text-fg"
          >
            <X size={16} />
          </button>
        </div>

        <div className="p-4 space-y-4">
          {/* Format note */}
          <p className="text-xs text-muted">
            Supported formats:{" "}
            <code className="rounded bg-panel-2 px-1 text-fg">.geojson</code>,{" "}
            <code className="rounded bg-panel-2 px-1 text-fg">.json</code>,{" "}
            <code className="rounded bg-panel-2 px-1 text-fg">.zip</code> (shapefile with .shp,
            .dbf, .prj).
          </p>

          {/* Drop zone */}
          <div
            onDragOver={(e) => {
              e.preventDefault();
              setIsDragging(true);
            }}
            onDragLeave={() => setIsDragging(false)}
            onDrop={handleDrop}
            onClick={() => inputRef.current?.click()}
            role="button"
            tabIndex={0}
            aria-label="Drop area for boundary file"
            onKeyDown={(e) => e.key === "Enter" && inputRef.current?.click()}
            className={cn(
              "flex min-h-[140px] cursor-pointer flex-col items-center justify-center gap-3 rounded-lg border-2 border-dashed transition-colors",
              isDragging
                ? "border-accent bg-accent/5"
                : "border-border hover:border-accent/50 hover:bg-panel-2",
            )}
          >
            <FileArrowUp
              size={32}
              weight="thin"
              className={cn("transition-colors", isDragging ? "text-accent" : "text-muted")}
            />
            <p className="text-sm text-muted">
              {parse.status === "parsing" ? (
                <span className="text-accent">Parsing file...</span>
              ) : fileName ? (
                <span className="text-fg">{fileName}</span>
              ) : (
                "Drop file here or click to browse"
              )}
            </p>
          </div>

          {/* Hidden file input */}
          <input
            ref={inputRef}
            type="file"
            accept=".geojson,.json,.zip"
            className="sr-only"
            onChange={handleInputChange}
            aria-hidden="true"
          />

          {/* Parse result feedback */}
          {parse.status === "done" && parse.geometry && (
            <div className="flex items-center gap-2 rounded-md border border-border bg-panel-2 px-3 py-2">
              <span className="min-w-0 flex-1 truncate text-sm text-fg">{parse.label}</span>
              {geometryTypeBadge(parse.geometry.type)}
            </div>
          )}

          {parse.status === "error" && (
            <p className="rounded-md border border-critical/30 bg-critical/10 px-3 py-2 text-xs text-critical">
              {parse.error}
            </p>
          )}
        </div>

        {/* Footer */}
        <div className="flex justify-end gap-2 border-t border-border px-4 py-3">
          <Button variant="ghost" onClick={onClose}>
            Cancel
          </Button>
          <Button
            variant="primary"
            onClick={handleApply}
            disabled={parse.status !== "done" || !parse.geometry}
          >
            Apply AOI
          </Button>
        </div>
      </div>
    </div>
  );
}
