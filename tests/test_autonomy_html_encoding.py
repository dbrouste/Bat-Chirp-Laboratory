"""HTML encoding failures must leave existing reports untouched."""

import plotly.graph_objects as go
import pytest

from laboratory.report import write_html


def make_report(file, error):
    return {
        "totals": {"files_evaluated": 1, "annotated": 0, "candidates": 0,
                   "matched": 0, "tracked": 0,
                   "weighted_detector_recall": None,
                   "weighted_full_pipeline_within_tolerance_fraction": None},
        "per_file": [{"file": file, "error": error}],
    }


@pytest.mark.parametrize("existing", [False, True])
@pytest.mark.parametrize("surrogate", ["\ud800", "\udfff"])
@pytest.mark.parametrize("field", ["file", "error"])
def test_encoding_failure_preserves_html(tmp_path, monkeypatch, existing,
                                         surrogate, field):
    path = tmp_path / "report.html"
    previous = b"previous HTML\r\n"
    if existing:
        path.write_bytes(previous)
    monkeypatch.setattr(go.Figure, "to_html", lambda *args, **kwargs: "<div>plot</div>")
    values = {"file": "sample", "error": "failed"}
    values[field] += surrogate

    with pytest.raises(UnicodeEncodeError):
        write_html(make_report(**values), path)

    if existing:
        assert path.read_bytes() == previous
    else:
        assert not path.exists()


def test_valid_unicode_html_is_written(tmp_path, monkeypatch):
    path = tmp_path / "report.html"
    path.write_bytes(b"previous HTML\n")
    monkeypatch.setattr(go.Figure, "to_html", lambda *args, **kwargs: "<div>plot</div>")

    write_html(make_report("\u00e9t\u00e9 \U0001f987", "\u00e9chec <test>"), path)

    html = path.read_text(encoding="utf-8")
    assert "\u00e9t\u00e9 \U0001f987" in html
    assert "\u00e9chec &lt;test&gt;" in html
    assert html.endswith("</body></html>")
