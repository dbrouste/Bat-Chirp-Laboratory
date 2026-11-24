"""Hover identifiers stay literal without affecting report scores."""

from copy import deepcopy

import plotly.graph_objects as go
import pytest

from laboratory.report import write_html


@pytest.mark.parametrize("tracked", [True, False])
@pytest.mark.parametrize(
    "file_id, chirp_id, expected",
    [
        ('<b>A&B</b>"\'.wav', '<i>chirp &amp;</i>"\'',
         '&lt;b&gt;A&amp;B&lt;/b&gt;&quot;&#x27;.wav : '
         '&lt;i&gt;chirp &amp;amp;&lt;/i&gt;&quot;&#x27;'),
        ("plain.wav", 42, "plain.wav : 42"),
    ],
)
def test_hover_identifiers_are_escaped(tmp_path, monkeypatch, tracked,
                                       file_id, chirp_id, expected):
    report = {
        "details": [{"file": file_id, "chirp_id": chirp_id,
                     "tracking_success": tracked, "snr_max_db": 12.5,
                     "mean_normalized_error": 0.25}],
        "totals": {"files_evaluated": 1, "annotated": 1, "candidates": 1,
                   "matched": 1, "tracked": int(tracked),
                   "weighted_detector_recall": 1.0,
                   "weighted_full_pipeline_within_tolerance_fraction": 0.75},
        "per_file": [],
    }
    original = deepcopy(report)
    figures = []

    def capture_plot(figure, **kwargs):
        figures.append(figure)
        return "<div>plot</div>"

    monkeypatch.setattr(go.Figure, "to_html", capture_plot)
    write_html(report, tmp_path / "report.html")

    trace = figures[0].data[0 if tracked else 1]
    assert list(trace.text) == [expected]
    assert list(trace.x) == [12.5]
    assert list(trace.y) == [0.25 if tracked else 0]
    assert report == original
