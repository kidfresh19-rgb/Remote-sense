"""Alerting: pure threshold evaluation, unit-testable with zero DB.

Two families live here. `evaluate_health` watches the pipeline-health summary (Phase 7, R-4:
dead-lettered pushes, backfill backlog). `evaluate_field` watches per-field agronomy over the index
time series (NDVI drop vs the prior pass and vs the season baseline, NDMI water stress, statistical
anomalies) plus an optional weather water-deficit check. Both stay pure and take primitive inputs,
so rs_core keeps no upward dependency on rs_analysis or rs_weather: a caller extracts the readings
and the weather totals and feeds them in. Fired alerts go to an `AlertSink` (logging by default; an
AgriTrack/webhook sink drops in behind the same interface)."""

from __future__ import annotations

import statistics
from collections.abc import Sequence
from dataclasses import dataclass
from datetime import date
from typing import Protocol, runtime_checkable

from rs_core.logging import get_logger

# Operational defaults, tuned conservatively. A dead-lettered gateway push is always actionable (a
# failed delivery awaiting retry, R-2). A backfill backlog past this fraction of fields means
# collection is falling behind and coverage is degrading.
MAX_DEAD_LETTERS = 0
MAX_AWAITING_BACKFILL_FRACTION = 0.5


@dataclass(frozen=True)
class HealthAlert:
    """One fired pipeline-health alert. `level` is "warning" or "critical"; `code` is a stable
    machine key; `message` is human-readable."""

    level: str
    code: str
    message: str


def evaluate_health(
    summary: dict[str, int],
    *,
    max_dead_letters: int = MAX_DEAD_LETTERS,
    max_awaiting_fraction: float = MAX_AWAITING_BACKFILL_FRACTION,
) -> list[HealthAlert]:
    """Fire alerts from a `pipeline_health` summary (fields / awaiting_backfill / dead_letters).
    Returns an empty list when the pipeline is healthy."""
    alerts: list[HealthAlert] = []

    dead = summary.get("dead_letters", 0)
    if dead > max_dead_letters:
        alerts.append(
            HealthAlert(
                level="critical",
                code="gateway_dead_letters",
                message=f"{dead} gateway push(es) dead-lettered and awaiting retry",
            )
        )

    fields = summary.get("fields", 0)
    awaiting = summary.get("awaiting_backfill", 0)
    if fields > 0 and awaiting / fields > max_awaiting_fraction:
        pct = round(100 * awaiting / fields)
        alerts.append(
            HealthAlert(
                level="warning",
                code="backfill_backlog",
                message=f"{awaiting}/{fields} fields ({pct}%) awaiting backfill",
            )
        )

    return alerts


# -- Per-field agronomic alerts (Tier 1) ----------------------------------------------------------

# Conservative operational defaults (heuristic monitoring, not agronomic classification, which lives
# in rs_interpret). Drops are in absolute index units between clear passes.
MIN_CLEAR_FOR_ALERT = 0.5  # cloud-thinned passes are not a reliable signal; skip them
NDVI_DROP_WARNING = 0.15
NDVI_DROP_CRITICAL = 0.30
NDVI_BASELINE_WINDOW = 5  # clear passes before the latest that define the recent baseline
NDMI_DRY_THRESHOLD = 0.0  # NDMI mean at/below this means low canopy moisture (water stress likely)
ANOMALY_Z = 2.0
ANOMALY_MIN_HISTORY = 4
WATER_DEFICIT_RATIO = 0.5  # rainfall below this fraction of reference ET over the window


@dataclass(frozen=True)
class PassReading:
    """One usable pass for a field+index: the zonal-mean index value on `pass_date` and the
    clear-pixel fraction. The caller builds these from stored stats; the rules skip low-confidence
    passes below `MIN_CLEAR_FOR_ALERT`."""

    pass_date: date
    index: str
    mean: float
    clear_fraction: float


@dataclass(frozen=True)
class FieldAlert:
    """One fired per-field alert. `level` is "info" | "warning" | "critical"; `code` is a stable
    machine key; `index`/`pass_date` give context when the alert is tied to a pass."""

    field_id: str
    level: str
    code: str
    message: str
    index: str | None = None
    pass_date: date | None = None


def _usable(readings: Sequence[PassReading], index: str, min_clear: float) -> list[PassReading]:
    return sorted(
        (r for r in readings if r.index == index and r.clear_fraction >= min_clear),
        key=lambda r: r.pass_date,
    )


def ndvi_drop_vs_prior(
    field_id: str,
    readings: Sequence[PassReading],
    *,
    min_clear: float = MIN_CLEAR_FOR_ALERT,
    warn_drop: float = NDVI_DROP_WARNING,
    crit_drop: float = NDVI_DROP_CRITICAL,
) -> list[FieldAlert]:
    """A significant NDVI fall between the two most recent clear passes (sudden vegetation loss:
    stress, damage, or an unreported harvest)."""
    usable = _usable(readings, "ndvi", min_clear)
    if len(usable) < 2:
        return []
    prev, latest = usable[-2], usable[-1]
    drop = prev.mean - latest.mean
    if drop < warn_drop:
        return []
    level = "critical" if drop >= crit_drop else "warning"
    return [
        FieldAlert(
            field_id=field_id,
            level=level,
            code="ndvi_drop_vs_prior",
            message=(
                f"NDVI fell {drop:.2f} (from {prev.mean:.2f} to {latest.mean:.2f}) "
                "since the previous clear pass"
            ),
            index="ndvi",
            pass_date=latest.pass_date,
        )
    ]


def ndvi_drop_vs_baseline(
    field_id: str,
    readings: Sequence[PassReading],
    *,
    window: int = NDVI_BASELINE_WINDOW,
    min_clear: float = MIN_CLEAR_FOR_ALERT,
    min_drop: float = NDVI_DROP_WARNING,
) -> list[FieldAlert]:
    """The latest NDVI sitting well below the median of the recent clear passes (a slower decline
    the pass-to-pass rule can miss)."""
    usable = _usable(readings, "ndvi", min_clear)
    if len(usable) < 3:
        return []
    latest = usable[-1]
    prior = usable[-(window + 1) : -1]
    baseline = statistics.median([r.mean for r in prior])
    drop = baseline - latest.mean
    if drop < min_drop:
        return []
    return [
        FieldAlert(
            field_id=field_id,
            level="warning",
            code="ndvi_below_baseline",
            message=(
                f"NDVI {latest.mean:.2f} is {drop:.2f} below the recent baseline {baseline:.2f}"
            ),
            index="ndvi",
            pass_date=latest.pass_date,
        )
    ]


def ndmi_water_stress(
    field_id: str,
    readings: Sequence[PassReading],
    *,
    dry_below: float = NDMI_DRY_THRESHOLD,
    min_clear: float = MIN_CLEAR_FOR_ALERT,
) -> list[FieldAlert]:
    """Latest NDMI at/below the dryness threshold: low canopy moisture, water stress likely."""
    usable = _usable(readings, "ndmi", min_clear)
    if not usable:
        return []
    latest = usable[-1]
    if latest.mean > dry_below:
        return []
    return [
        FieldAlert(
            field_id=field_id,
            level="warning",
            code="ndmi_water_stress",
            message=f"NDMI {latest.mean:.2f} indicates low canopy moisture (water stress likely)",
            index="ndmi",
            pass_date=latest.pass_date,
        )
    ]


def anomaly_zscore(
    field_id: str,
    readings: Sequence[PassReading],
    index: str,
    *,
    z: float = ANOMALY_Z,
    min_history: int = ANOMALY_MIN_HISTORY,
    min_clear: float = MIN_CLEAR_FOR_ALERT,
) -> list[FieldAlert]:
    """The latest reading for `index` deviating from its own history by more than `z` standard
    deviations (an outlier versus the field's normal range)."""
    usable = _usable(readings, index, min_clear)
    if len(usable) < min_history + 1:
        return []
    history = [r.mean for r in usable[:-1]]
    latest = usable[-1]
    mu = statistics.fmean(history)
    sigma = statistics.pstdev(history)
    if sigma == 0:
        return []
    score = (latest.mean - mu) / sigma
    if abs(score) < z:
        return []
    return [
        FieldAlert(
            field_id=field_id,
            level="warning",
            code="anomaly",
            message=f"{index.upper()} {latest.mean:.2f} is {score:+.1f} SD from its history "
            f"(mean {mu:.2f})",
            index=index,
            pass_date=latest.pass_date,
        )
    ]


def water_deficit(
    field_id: str,
    *,
    precip_mm: float,
    et0_mm: float,
    ratio: float = WATER_DEFICIT_RATIO,
) -> list[FieldAlert]:
    """A weather water-deficit check: rainfall over the window below `ratio` of reference ET0. The
    caller computes `precip_mm` and `et0_mm` from a weather series (rs_weather.agro), so this stays
    pure and rs_core keeps no dependency on rs_weather."""
    if et0_mm <= 0 or precip_mm >= ratio * et0_mm:
        return []
    return [
        FieldAlert(
            field_id=field_id,
            level="warning",
            code="water_deficit",
            message=(
                f"rainfall {precip_mm:.0f} mm is below {ratio:.0%} of reference ET {et0_mm:.0f} mm"
            ),
        )
    ]


def evaluate_field(
    field_id: str,
    readings: Sequence[PassReading],
    *,
    recent_precip_mm: float | None = None,
    recent_et0_mm: float | None = None,
    anomaly_indices: Sequence[str] = ("ndre", "ndmi"),
) -> list[FieldAlert]:
    """All per-field alerts for one field. The NDVI drop rules and the NDMI stress rule always run;
    anomaly detection runs for `anomaly_indices` (NDVI is already covered by the drop rules); the
    water-deficit check runs when weather totals are supplied (from rs_weather.agro)."""
    alerts: list[FieldAlert] = []
    alerts += ndvi_drop_vs_prior(field_id, readings)
    alerts += ndvi_drop_vs_baseline(field_id, readings)
    alerts += ndmi_water_stress(field_id, readings)
    for index in anomaly_indices:
        alerts += anomaly_zscore(field_id, readings, index)
    if recent_precip_mm is not None and recent_et0_mm is not None:
        alerts += water_deficit(field_id, precip_mm=recent_precip_mm, et0_mm=recent_et0_mm)
    return alerts


@runtime_checkable
class AlertSink(Protocol):
    """Where fired field alerts go. The default logs them; an AgriTrack push or a webhook notifier
    drops in behind this interface (⚑ CONFIRM: the AgriTrack delivery contract is parked, like the
    gateway push)."""

    def emit(self, alert: FieldAlert) -> None: ...


class RecordingAlertSink:
    """Collects alerts in memory (tests, and batching before a single delivery)."""

    def __init__(self) -> None:
        self.alerts: list[FieldAlert] = []

    def emit(self, alert: FieldAlert) -> None:
        self.alerts.append(alert)


class LoggingAlertSink:
    """Emits each alert as a structured log line, so a log-based notifier can act on it (the same
    pattern the pipeline-health endpoint uses for `HealthAlert`)."""

    def __init__(self) -> None:
        self._log = get_logger("alerts.field")

    def emit(self, alert: FieldAlert) -> None:
        self._log.warning(
            "field.alert",
            field_id=alert.field_id,
            level=alert.level,
            code=alert.code,
            index=alert.index,
            message=alert.message,
        )
