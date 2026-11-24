"""HTML diagnostics survive Windows text-mode newline translation."""

from copy import deepcopy
from pathlib import Path

import plotly.graph_objects as go
import pytest

from laboratory.report import write_html


@pytest.mark.parametrize(
    "error",
    [
        "Traceback:\r\n    <failed>\r\n\r\n\tcaused by:\r\n  ",
        "Traceback:\n    <failed>\n\n\tcaused by:\n  ",
        "Traceback:\r\n    <failed>\n\r\n\tcaused by:\r\n  ",
    ],
)
def test_windows_newline_translation_preserves_diagnostics(tmp_path, monkeypatch,
                                                          error):
    report = {
        "totals": {
            "files_evaluated": 0, "annotated": 0, "candidates": 0,
            "matched": 0, "tracked": 0,
            "weighted_detector_recall": None,
            "weighted_full_pipeline_within_tolerance_fraction": None,
        },
        "per_file": [{"file": "sample.wav", "error": error}],
    }
    original = deepcopy(report)

    def windows_write_text(path, text, *, encoding):
        # Exercise real text-mode translation on every test platform.
        with path.open("w", encoding=encoding, newline="\r\n") as stream:
            return stream.write(text)

    monkeypatch.setattr(Path, "write_text", windows_write_text)
    monkeypatch.setattr(go.Figure, "to_html", lambda *args, **kwargs: "<div>plot</div>")
    path = tmp_path / "report.html"

    write_html(report, path)

    written = path.read_bytes()
    assert b"\r\r\n" not in written
    assert (
        b"<li style='white-space:pre-wrap'>sample.wav: "
        b"Traceback:\r\n    &lt;failed&gt;\r\n\r\n\tcaused by:\r\n  </li>"
    ) in written
    assert report == original
