/** Map an `overall_health` label (healthy | moderate | stressed | critical) to a Badge tone, so the
 *  farm push modal and the dashboard farm cards colour health the same way. */
export type HealthTone = "positive" | "caution" | "critical" | "neutral";

export function healthTone(health: string | null): HealthTone {
  switch (health) {
    case "healthy":
      return "positive";
    case "moderate":
      return "caution";
    case "stressed":
    case "critical":
      return "critical";
    default:
      return "neutral";
  }
}
