/** Ward Watch presentation vocab: the 2x2 movement label and the cohort fallback ladder, mirrored
 *  from rs_core.movement.MovementLabel and rs_core.cohorts.CohortLevel so the cockpit speaks the
 *  same language as the engine (PRD 0003 §6-§7). */

type Tone = "neutral" | "positive" | "caution" | "critical" | "accent";

export interface MovementMeta {
  label: string;
  tone: Tone;
  blurb: string;
}

/** Display label, badge tone and a one-line meaning per movement label, keyed by the wire value.
 *  Severity order (idiosyncratic worst) matches the triage ranking in rs_core.triage. */
const MOVEMENT_META: Record<string, MovementMeta> = {
  idiosyncratic: {
    label: "Idiosyncratic",
    tone: "critical",
    blurb: "Falling while its cohort holds - a household-specific problem. Officer visit.",
  },
  systemic: {
    label: "Systemic",
    tone: "caution",
    blurb: "Falling with its whole cohort - a food-security signal, not one household.",
  },
  resilient: {
    label: "Resilient",
    tone: "positive",
    blurb: "Holding while its cohort falls - doing better than its peers.",
  },
  nominal: {
    label: "Nominal",
    tone: "neutral",
    blurb: "Moving with its cohort - nothing to flag.",
  },
};

export function movementMeta(label: string): MovementMeta {
  return MOVEMENT_META[label] ?? { label, tone: "neutral", blurb: "" };
}

/** How wide the peer cohort had to widen to reach quorum (the fallback ladder). Narrower = a more
 *  local, more trustworthy comparison. Mirrors rs_core.cohorts.CohortLevel. */
const COHORT_LEVEL_LABEL: Record<string, string> = {
  ward_crop_window: "Ward · crop · window",
  ward_crop: "Ward · crop",
  district_crop: "District · crop",
  natural_region_crop: "Region · crop",
};

export function cohortLevelLabel(level: string): string {
  return COHORT_LEVEL_LABEL[level] ?? level;
}
