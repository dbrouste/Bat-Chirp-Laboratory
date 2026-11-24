"""Strict summary serialization rejects non-finite values before exporting."""
from pathlib import Path

import pytest

from laboratory.local_campaign import save_reports


def _record(value):
    return dict(window=3, threshold=value, scenario="example", region="all",
                total=1, valid=1, baseline_flagged=0, new_alerts=0,
                legacy_new_alerts=0)


@pytest.mark.parametrize("value", [float("nan"), float("inf"), float("-inf")],
                         ids=["nan", "positive_infinity", "negative_infinity"])
@pytest.mark.parametrize("field", ["definition", "runs", "results"])
@pytest.mark.parametrize("existing", [False, True])
def test_nonfinite_summary_preserves_exports(
    tmp_path, monkeypatch, value, field, existing
):
    scores = tmp_path / "campaign_scores.csv"
    summary = tmp_path / "campaign_summary.json"
    report = tmp_path / "campaign_report.html"
    if existing:
        scores.write_bytes(b"previous CSV\r\n")
        summary.write_bytes(b"previous summary\n")
        report.write_bytes(b"previous HTML\n")
    before = {path: path.stat() for path in tmp_path.iterdir()}
    text_writes = []
    write_text = Path.write_text

    def track_write(path, text, **kwargs):
        text_writes.append(path)
        return write_text(path, text, **kwargs)

    monkeypatch.setattr(Path, "write_text", track_write)
    records = [_record(value)] if field == "results" else []
    runs = [{"value": value}] if field == "runs" else []
    definition = {"value": value} if field == "definition" else {}

    with pytest.raises(ValueError, match="Out of range float values"):
        save_reports(tmp_path, records, runs, definition)

    assert set(tmp_path.iterdir()) == set(before)
    assert text_writes == []
    # Verify summary preservation through metadata without reading JSON files.
    assert {path: path.stat() for path in before} == before
    if existing:
        assert scores.read_bytes() == b"previous CSV\r\n"
        assert report.read_bytes() == b"previous HTML\n"


@pytest.mark.parametrize("value", [-1.5, 0.0, 2.5])
def test_finite_summary_exports(tmp_path, monkeypatch, value):
    summary_texts = []
    write_text = Path.write_text

    def track_write(path, text, **kwargs):
        if path.name == "campaign_summary.json":
            summary_texts.append(text)
        return write_text(path, text, **kwargs)

    monkeypatch.setattr(Path, "write_text", track_write)
    save_reports(tmp_path, [_record(value)], [{"value": value}], {"value": value})

    assert len(summary_texts) == 1
    assert summary_texts[0].count(f'"value": {value}') == 2
    assert f'"threshold": {value}' in summary_texts[0]
    assert {path.name for path in tmp_path.iterdir()} == {
        "campaign_scores.csv", "campaign_summary.json", "campaign_report.html"
    }
