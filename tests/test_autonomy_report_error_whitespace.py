"""Error diagnostics preserve whitespace and remain escaped in HTML reports."""

from copy import deepcopy
from pathlib import Path

import plotly.graph_objects as go
import pytest

from laboratory.report import write_html


@pytest.mark.parametrize(
    "error, expected",
    [
        ('Traceback:\n    <script>alert("x")</script> & \'detail\'\n\n\tcaused by:\n  ',
         'Traceback:\n    &lt;script&gt;alert(&quot;x&quot;)&lt;/script&gt; '
         '&amp; &#x27;detail&#x27;\n\n\tcaused by:\n  '),
        ("single line", "single line"),
        (42, "42"),
        ("", ""),
    ],
)
def test_error_whitespace_and_escaping(monkeypatch, error, expected):
    report = {
        "totals": {
            "files_evaluated": 0, "annotated": 0, "candidates": 0,
            "matched": 0, "tracked": 0,
            "weighted_detector_recall": None,
            "weighted_full_pipeline_within_tolerance_fraction": None,
        },
        "per_file": [
            {"file": '<b>A&B</b>"\'.wav', "error": error},
            {"file": "successful.wav"},
        ],
    }
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
    assert (
        "<ul><li style='white-space:pre-wrap'>"
        "&lt;b&gt;A&amp;B&lt;/b&gt;&quot;&#x27;.wav: "
        f"{expected}</li></ul>"
    ) in html
    assert "<script>" not in html
    assert "successful.wav" not in html
    assert report == original
