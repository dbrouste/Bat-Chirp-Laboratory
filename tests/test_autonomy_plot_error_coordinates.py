"""Keep plotted error coordinates finite without altering source scores."""

from copy import deepcopy

import plotly.graph_objects as go

from laboratory.report import write_html


def test_plot_filters_invalid_tracked_errors_and_keeps_arrays_aligned(tmp_path, monkeypatch):
    errors = [
        {}, {"mean_normalized_error": None},
        {"mean_normalized_error": float("nan")},
        {"mean_normalized_error": float("inf")},
        {"mean_normalized_error": float("-inf")},
        {"mean_normalized_error": 0},
        {"mean_normalized_error": 0.0},
        {"mean_normalized_error": 0.75},
    ]
    report = {
        "details": [
            {"file": f"sample-{matched}-{index}", "chirp_id": index,
             "tracking_success": matched, "snr_max_db": index - 5, **error}
            for matched in (True, False)
            for index, error in enumerate(errors)
        ],
        "totals": {
            "files_evaluated": 1, "annotated": 16, "candidates": 16,
            "matched": 16, "tracked": 8,
            "weighted_detector_recall": 1.0,
            "weighted_full_pipeline_within_tolerance_fraction": 0.5,
            "mean_tracking_error_khz": 0.25,
        },
        "per_file": [],
    }
    original = deepcopy(report)
    figures = []

    def capture_plot(figure, **kwargs):
        figures.append(figure)
        return "<div>plot</div>"

    monkeypatch.setattr(go.Figure, "to_html", capture_plot)

    write_html(report, tmp_path / "report.html")

    assert len(figures) == 1
    tracked, untracked = figures[0].data
    assert list(tracked.x) == [0, 1, 2]
    assert list(tracked.y) == [0, 0.0, 0.75]
    assert list(tracked.text) == [f"sample-True-{i} : {i}" for i in (5, 6, 7)]
    assert list(untracked.x) == list(range(-5, 3))
    assert list(untracked.y) == [0] * 8
    assert list(untracked.text) == [f"sample-False-{i} : {i}" for i in range(8)]
    assert report == original
