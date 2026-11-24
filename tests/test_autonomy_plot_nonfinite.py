"""Plot only finite SNR coordinates without changing the source scores."""

from copy import deepcopy

import plotly.graph_objects as go

from laboratory.report import write_html


def test_plot_filters_nonfinite_snr_in_both_tracking_groups(tmp_path, monkeypatch):
    values = [None, float("nan"), float("inf"), float("-inf"), -3.5, 0.0, 12.0]
    report = {
        "details": [
            {"file": f"sample-{matched}-{index}", "chirp_id": index,
             "tracking_success": matched, "snr_max_db": snr,
             "mean_normalized_error": index / 10 if matched else None}
            for matched in (True, False)
            for index, snr in enumerate(values)
        ],
        "totals": {
            "files_evaluated": 1, "annotated": 14, "candidates": 14,
            "matched": 14, "tracked": 7,
            "weighted_detector_recall": 1.0,
            "weighted_full_pipeline_within_tolerance_fraction": 0.5,
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
    for trace, matched, label in ((tracked, True, "Suivi"),
                                  (untracked, False, "Non suivi")):
        assert trace.name == label
        assert list(trace.x) == [-3.5, 0.0, 12.0]
        assert list(trace.y) == ([0.4, 0.5, 0.6] if matched else [0, 0, 0])
        assert list(trace.text) == [
            f"sample-{matched}-{index} : {index}" for index in (4, 5, 6)
        ]
    assert report == original
