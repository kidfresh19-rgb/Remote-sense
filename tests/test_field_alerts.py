"""Tests for the per-field agronomic alert rules (Tier 1, T1.2). Pure: synthetic pass readings in,
FieldAlerts out, zero DB and zero network."""

from __future__ import annotations

from datetime import date, timedelta

from rs_core.alerts import (
    FieldAlert,
    LoggingAlertSink,
    PassReading,
    RecordingAlertSink,
    anomaly_zscore,
    evaluate_field,
    ndmi_water_stress,
    ndvi_drop_vs_baseline,
    ndvi_drop_vs_prior,
    water_deficit,
)

_D0 = date(2024, 1, 1)


def _ndvi(values: list[float], *, clear: float = 0.9) -> list[PassReading]:
    return [
        PassReading(
            pass_date=_D0 + timedelta(days=5 * i), index="ndvi", mean=v, clear_fraction=clear
        )
        for i, v in enumerate(values)
    ]


def test_ndvi_drop_vs_prior_warns_and_escalates():
    warn = ndvi_drop_vs_prior("F1", _ndvi([0.70, 0.52]))  # drop 0.18
    assert [a.code for a in warn] == ["ndvi_drop_vs_prior"]
    assert warn[0].level == "warning"
    crit = ndvi_drop_vs_prior("F1", _ndvi([0.80, 0.40]))  # drop 0.40
    assert crit[0].level == "critical"


def test_ndvi_drop_vs_prior_silent_on_small_change():
    assert ndvi_drop_vs_prior("F1", _ndvi([0.70, 0.62])) == []  # drop 0.08 < 0.15


def test_ndvi_drop_vs_prior_ignores_cloudy_latest():
    # The latest pass is cloud-thinned, so it is not a reliable signal and is skipped.
    readings = _ndvi([0.70]) + [
        PassReading(pass_date=_D0 + timedelta(days=5), index="ndvi", mean=0.30, clear_fraction=0.2)
    ]
    assert ndvi_drop_vs_prior("F1", readings) == []


def test_ndvi_drop_vs_baseline_flags_slow_decline():
    # Stable around 0.70, then the latest sinks to 0.50: below the median baseline by 0.20.
    alerts = ndvi_drop_vs_baseline("F1", _ndvi([0.70, 0.72, 0.69, 0.71, 0.50]))
    assert [a.code for a in alerts] == ["ndvi_below_baseline"]


def test_ndmi_water_stress_fires_when_dry():
    dry = [PassReading(_D0, "ndmi", -0.05, 0.9)]
    wet = [PassReading(_D0, "ndmi", 0.25, 0.9)]
    assert ndmi_water_stress("F1", dry)[0].code == "ndmi_water_stress"
    assert ndmi_water_stress("F1", wet) == []


def test_anomaly_zscore_flags_outlier_against_history():
    history = [PassReading(_D0 + timedelta(days=5 * i), "ndre", 0.40, 0.9) for i in range(5)]
    history.append(PassReading(_D0 + timedelta(days=30), "ndre", 0.40, 0.9))
    # A flat history then a sharp outlier.
    spike = [*history[:-1], PassReading(_D0 + timedelta(days=40), "ndre", 0.10, 0.9)]
    # Give the flat history a little variance so sigma > 0.
    spike[1] = PassReading(spike[1].pass_date, "ndre", 0.42, 0.9)
    spike[2] = PassReading(spike[2].pass_date, "ndre", 0.38, 0.9)
    alerts = anomaly_zscore("F1", spike, "ndre")
    assert [a.code for a in alerts] == ["anomaly"]


def test_anomaly_zscore_needs_enough_history():
    short = [PassReading(_D0 + timedelta(days=5 * i), "ndre", 0.4, 0.9) for i in range(3)]
    assert anomaly_zscore("F1", short, "ndre") == []


def test_water_deficit_fires_when_rain_below_half_et0():
    assert water_deficit("F1", precip_mm=10.0, et0_mm=40.0)[0].code == "water_deficit"
    assert water_deficit("F1", precip_mm=30.0, et0_mm=40.0) == []  # 30 >= 0.5*40
    assert water_deficit("F1", precip_mm=0.0, et0_mm=0.0) == []  # no ET reference


def test_evaluate_field_combines_rules_and_weather():
    readings = _ndvi([0.70, 0.50]) + [PassReading(_D0 + timedelta(days=5), "ndmi", -0.1, 0.9)]
    alerts = evaluate_field("F1", readings, recent_precip_mm=5.0, recent_et0_mm=40.0)
    codes = {a.code for a in alerts}
    assert "ndvi_drop_vs_prior" in codes
    assert "ndmi_water_stress" in codes
    assert "water_deficit" in codes
    assert all(a.field_id == "F1" for a in alerts)


def test_recording_and_logging_sinks_emit():
    rec = RecordingAlertSink()
    alert = FieldAlert(field_id="F1", level="warning", code="x", message="m")
    rec.emit(alert)
    assert rec.alerts == [alert]
    LoggingAlertSink().emit(alert)  # structured log, no raise
