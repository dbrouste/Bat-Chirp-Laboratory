"""HTML plots retain both groups from single-use detail iterables."""

import plotly.graph_objects as go

from laboratory.report import write_html


def test_details_generator_retains_rows_in_both_traces(tmp_path, monkeypatch):
    rows = [
        {"file": "untracked-a", "chirp_id": 1, "tracking_success": False,
         "snr_max_db": 10.0, "mean_normalized_error": None},
        {"file": "tracked-a", "chirp_id": 2, "tracking_success": True,
         "snr_max_db": 20.0, "mean_normalized_error": 0.25},
        {"file": "untracked-b", "chirp_id": 3, "tracking_success": False,
         "snr_max_db": 30.0, "mean_normalized_error": None},
        {"file": "tracked-b", "chirp_id": 4, "tracking_success": True,
         "snr_max_db": 40.0, "mean_normalized_error": 0.5},
    ]
    report = {
        "details": (row for row in rows),
        "totals": {"files_evaluated": 4, "annotated": 4, "candidates": 4,
                   "matched": 4, "tracked": 2,
                   "weighted_detector_recall": 1.0,
                   "weighted_full_pipeline_within_tolerance_fraction": 0.5},
        "per_file": [],
    }
    figures = []

    def capture_plot(figure, **kwargs):
        figures.append(figure)
        return "<div>plot</div>"

    monkeypatch.setattr(go.Figure, "to_html", capture_plot)
    write_html(report, tmp_path / "report.html")

    tracked, untracked = figures[0].data
    assert tracked.name == "Suivi"
    assert list(tracked.x) == [20.0, 40.0]
    assert list(tracked.y) == [0.25, 0.5]
    assert list(tracked.text) == ["tracked-a : 2", "tracked-b : 4"]
    assert untracked.name == "Non suivi"
    assert list(untracked.x) == [10.0, 30.0]
    assert list(untracked.y) == [0, 0]
    assert list(untracked.text) == ["untracked-a : 1", "untracked-b : 3"]
