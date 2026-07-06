import { seriesFromTimeseries } from "@/lib/fieldHealth";
import { useAllIndexTimeseries } from "@/lib/queries";

import { FieldOverview } from "./aoi/FieldOverview";
import { ErrorState, LoadingRows } from "./states";

/** Workspace-side wrapper for the cross-index field-health Overview. Fans out the field's five
 *  index timeseries, normalises them, and hands the shared `FieldOverview` the same shape AOI Studio
 *  builds from a completed job. */
export function FieldHealthPanel({ fieldId }: { fieldId: string }) {
  const { byIndex, isLoading, isError } = useAllIndexTimeseries(fieldId);
  const series = seriesFromTimeseries(byIndex);
  const hasAny = Object.keys(series).length > 0;

  if (isLoading && !hasAny) return <LoadingRows rows={6} />;
  if (isError && !hasAny) {
    return <ErrorState error={new Error("Could not load this field's index history.")} />;
  }

  return (
    <FieldOverview
      series={series}
      emptyHint="No usable passes yet for this field. Collect its history to see a cross-index health overview."
    />
  );
}
