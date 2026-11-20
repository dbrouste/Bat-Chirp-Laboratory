"""Unavailable summary values have explicit labels while zero stays numeric."""

from copy import deepcopy

import plotly.graph_objects as go
import pytest

from laboratory.report import write_html


@pytest.mark.parametrize("geometry", [False, True])
@pytest.mark.parametrize("value", [None, 0, 0.0])
def test_summary_missing_values_and_zero(tmp_path, monkeypatch, geometry, value):
    report = {
        "metadata": {"scoring_protocol": "square_normals_v1" if geometry else "legacy"},
        "totals": {
            "files_evaluated": 1, "annotated": 0, "candidates": 0,
            "matched": 0, "tracked": 0,
            "mean_geometric_error_khz_eq": value,
            "mean_tracking_error_khz": value,
            "mean_geometric_coverage_fraction": value,
            "weighted_detector_recall": value,
            "weighted_full_pipeline_within_tolerance_fraction": value,
            "reviewed_no_chirp": {
                "wav_count": 1, "wavs_with_candidate_alert": 0,
                "candidate_alerts": 0, "wav_false_alarm_fraction": value,
                "candidate_alerts_per_wav": value,
                "candidate_alerts_per_hour": value,
            },
        },
        "per_file": [],
    }
    original = deepcopy(report)
    monkeypatch.setattr(go.Figure, "to_html", lambda *args, **kwargs: "<div>plot</div>")
    path = tmp_path / "report.html"

    write_html(report, path)

    html = path.read_text(encoding="utf-8")
    expected = "indisponible" if value is None else str(value)
    labels = [
        "Écart normal moyen (kHz_eq)" if geometry else "Écart vertical moyen (kHz)",
        "Rappel pondéré", "Suivi tolérance pondéré",
        "Fraction des WAV No chirp avec alerte", "Candidats par WAV No chirp",
        "Alertes candidates par heure No chirp",
    ]
    for label in labels:
        assert f"<th>{label}</th><td>{expected}</td>" in html
    coverage = "Couverture des normales" if geometry else "Couverture temporelle"
    coverage_value = expected if geometry else "indisponible"
    assert f"<th>{coverage}</th><td>{coverage_value}</td>" in html
    assert "<th>Suivis</th><td>0</td>" in html
    assert "<td>None</td>" not in html
    assert report == original
