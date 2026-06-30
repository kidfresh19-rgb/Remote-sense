/** Diagnosis controlled vocabularies for the capture form, mirrored from rs_core.diagnosis
 *  (PRD 0003 §12.9, backlog 0038). The `value`s MUST match the backend StrEnums exactly - the
 *  server validates and 422s an unknown value. ⚑ CONFIRM: these are candidate sets pending
 *  agronomist sign-off; keep in sync with packages/rs_core/diagnosis.py. */

export interface VocabOption {
  value: string;
  label: string;
}

export const CONDITIONS: VocabOption[] = [
  { value: "healthy", label: "Healthy" },
  { value: "water_stress", label: "Water stress" },
  { value: "waterlogging", label: "Waterlogging" },
  { value: "pest_damage", label: "Pest damage" },
  { value: "disease", label: "Disease" },
  { value: "nutrient_deficiency", label: "Nutrient deficiency" },
  { value: "weed_pressure", label: "Weed pressure" },
  { value: "poor_establishment", label: "Poor establishment" },
  { value: "crop_failure", label: "Crop failure" },
  { value: "other", label: "Other" },
];

export const CAUSES: VocabOption[] = [
  { value: "insufficient_rain", label: "Insufficient rain" },
  { value: "excess_rain", label: "Excess rain" },
  { value: "pest", label: "Pest" },
  { value: "disease", label: "Disease" },
  { value: "low_soil_fertility", label: "Low soil fertility" },
  { value: "input_access", label: "Input access" },
  { value: "late_planting", label: "Late planting" },
  { value: "weed_competition", label: "Weed competition" },
  { value: "unknown", label: "Unknown" },
  { value: "other", label: "Other" },
];

export const ACTIONS: VocabOption[] = [
  { value: "irrigate", label: "Irrigate" },
  { value: "improve_drainage", label: "Improve drainage" },
  { value: "apply_fertiliser", label: "Apply fertiliser" },
  { value: "pest_control", label: "Pest control" },
  { value: "disease_control", label: "Disease control" },
  { value: "weed_control", label: "Weed control" },
  { value: "replant", label: "Replant" },
  { value: "monitor", label: "Monitor" },
  { value: "refer_for_support", label: "Refer for support" },
  { value: "other", label: "Other" },
];
