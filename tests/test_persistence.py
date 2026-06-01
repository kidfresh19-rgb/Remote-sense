"""No-DB unit test for the worker's AnalysisOutput -> upsert-kwargs mapping (D3). The mapping is
pure, so it is verified here without a database; the actual write is covered in test_analysis_db."""

from __future__ import annotations

import inspect
import uuid
from datetime import date

from rs_analysis import AnalysisOutput
from rs_analysis.zonal import ZonalStats

from services.worker.persistence import analysis_upsert_kwargs


def _output() -> AnalysisOutput:
    return AnalysisOutput(
        index_name="ndre",
        formula_version="ndre/v1",
        resolution_m=20,
        clear_fraction=0.74,
        confidence="medium",
        stats=ZonalStats(count=128, mean=0.41, min=0.05, max=0.7, std=0.09, p10=0.2, p90=0.63),
    )


def test_mapping_flattens_stats_and_carries_provenance() -> None:
    field_id = uuid.uuid4()
    kwargs = analysis_upsert_kwargs(
        _output(),
        field_id=field_id,
        scene_id="S2A_X",
        pass_date=date(2024, 11, 2),
        geometry_version=3,
        provider="cdse",
        provider_scene_id="S2A_X",
        processing_mode="server_compute",
    )
    # stats flattened onto the row columns (note min -> min_val, max -> max_val)
    assert kwargs["mean"] == 0.41
    assert kwargs["min_val"] == 0.05
    assert kwargs["max_val"] == 0.7
    assert kwargs["std"] == 0.09
    assert kwargs["p10"] == 0.2
    assert kwargs["p90"] == 0.63
    # science + provenance carried through; resolution stored as float
    assert kwargs["index_name"] == "ndre"
    assert kwargs["formula_version"] == "ndre/v1"
    assert kwargs["resolution_m"] == 20.0
    assert isinstance(kwargs["resolution_m"], float)
    assert kwargs["clear_fraction"] == 0.74
    assert kwargs["confidence"] == "medium"
    assert kwargs["geometry_version"] == 3
    assert kwargs["field_id"] is field_id
    assert kwargs["processing_mode"] == "server_compute"
    assert kwargs["cog_uri"] is None


def test_mapping_keys_are_accepted_by_upsert_analysis() -> None:
    # Drift guard: every key produced here must be a parameter upsert_analysis accepts.
    from rs_core.repositories import upsert_analysis

    accepted = set(inspect.signature(upsert_analysis).parameters)
    produced = set(
        analysis_upsert_kwargs(
            _output(),
            field_id=uuid.uuid4(),
            scene_id="s",
            pass_date=date(2024, 1, 1),
            geometry_version=1,
            provider="cdse",
            provider_scene_id="s",
            processing_mode="mock",
        )
    )
    assert produced <= accepted
