"""Report explanations use the units of the selected scoring protocol."""

from copy import deepcopy
from pathlib import Path

import plotly.graph_objects as go
import pytest

from laboratory.report import write_html


@pytest.mark.parametrize("protocol", ["vertical_legacy", "square_normals_v1", None])
def test_protocol_explanation(monkeypatch, protocol):
    report = {
        "totals": {
            "files_evaluated": 1, "annotated": 1, "candidates": 1,
            "matched": 1, "tracked": 1,
            "mean_geometric_error_khz_eq": 0.25,
            "mean_tracking_error_khz": 0.5,
            "mean_geometric_coverage_fraction": 0.75,
            "weighted_detector_recall": 1.0,
            "weighted_full_pipeline_within_tolerance_fraction": 1.0,
        },
        "per_file": [],
    }
    if protocol is not None:
        report["metadata"] = {"scoring_protocol": protocol}
    original = deepcopy(report)
    writes = []

    def capture_write(path, text, **kwargs):
        writes.append((path, text, kwargs))
        return len(text)

    monkeypatch.setattr(Path, "write_text", capture_write)
    monkeypatch.setattr(go.Figure, "to_html", lambda *args, **kwargs: "<div>plot</div>")
    path = Path("report.html")

    write_html(report, path)

    assert len(writes) == 1
    written_path, html, kwargs = writes[0]
    assert written_path == path
    assert kwargs == {"encoding": "utf-8"}
    assert f"Protocole : {protocol}." in html
    if protocol == "square_normals_v1":
        assert "distances perpendiculaires en kHz_eq et une couverture explicite" in html
        assert "<th>Écart normal moyen (kHz_eq)</th><td>0.25</td>" in html
        assert "écarts verticaux" not in html
    else:
        assert "écarts verticaux de fréquence en kHz" in html
        assert "<th>Écart vertical moyen (kHz)</th><td>0.5</td>" in html
        assert "kHz_eq" not in html
        assert "perpendiculaires" not in html
        assert "couverture explicite" not in html
    assert "les seuils SNR restent exploratoires." in html
    assert "Les chirps non suivis ont une ordonnée zéro uniquement pour l'affichage." in html
    assert report == original
