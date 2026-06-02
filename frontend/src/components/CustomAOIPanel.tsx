import { Crosshair, Trash } from "@phosphor-icons/react";
import type { Geometry } from "geojson";

import { deleteCustomAOI, useCustomAOIs } from "@/lib/customAOIs";

import { SavedViews } from "./SavedViews";
import { Badge, IconButton } from "./ui";

interface CustomAOIPanelProps {
  onActivate: (geometry: Geometry) => void;
  onDelete: (id: string) => void;
}

export function CustomAOIPanel({ onActivate, onDelete }: CustomAOIPanelProps) {
  const aois = useCustomAOIs();

  function handleDelete(id: string) {
    deleteCustomAOI(id);
    onDelete(id);
  }

  return (
    <div className="shrink-0 border-t border-border">
      {/* Custom AOIs section */}
      <div className="flex items-center gap-2 px-3 py-2">
        <span className="text-xs font-semibold uppercase tracking-wide text-muted">
          Custom AOIs
        </span>
        {aois.length > 0 && (
          <Badge tone="neutral" className="text-[10px]">
            {aois.length}
          </Badge>
        )}
      </div>

      {aois.length === 0 ? (
        <p className="px-3 pb-2 text-xs text-muted">No custom AOIs saved</p>
      ) : (
        <ul className="max-h-44 overflow-y-auto px-1 pb-1">
          {aois.map((aoi) => (
            <li key={aoi.id} className="flex items-center gap-1">
              <span className="min-w-0 flex-1 truncate px-2 py-1.5 text-sm text-fg">
                {aoi.label}
              </span>
              <Badge tone="neutral" className="shrink-0 text-[10px] uppercase">
                {aoi.geometry.type}
              </Badge>
              <IconButton
                label={`Activate AOI: ${aoi.label}`}
                onClick={() => onActivate(aoi.geometry)}
                className="shrink-0"
              >
                <Crosshair size={13} />
              </IconButton>
              <IconButton
                label={`Delete AOI: ${aoi.label}`}
                onClick={() => handleDelete(aoi.id)}
                className="shrink-0 text-muted hover:text-critical"
              >
                <Trash size={13} />
              </IconButton>
            </li>
          ))}
        </ul>
      )}

      {/* Saved views subsection */}
      <SavedViews />
    </div>
  );
}
