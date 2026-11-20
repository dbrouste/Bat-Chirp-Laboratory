"""Regression coverage for exporting single-use report iterables."""
import csv
import json
from pathlib import Path

from laboratory.local_campaign import save_reports


def test_generator_rows_appear_in_all_report_formats(tmp_path, monkeypatch):
    records = [
        dict(window=3, threshold=1.5, scenario="aggregate-first", region="all",
             total=4, valid=4, baseline_flagged=1, new_alerts=2,
             legacy_new_alerts=1),
        dict(window=3, threshold=1.5, scenario="regional-only", region="middle",
             total=2, valid=2, baseline_flagged=0, new_alerts=1,
             legacy_new_alerts=0),
        dict(window=5, threshold=1.5, scenario="aggregate-last", region="all",
             total=8, valid=8, baseline_flagged=2, new_alerts=2,
             legacy_new_alerts=1),
    ]
    text_exports = {}

    def capture_text(path, text, **kwargs):
        text_exports[path.name] = text
        return len(text)

    # Inspect generated JSON in memory without reading JSON files.
    monkeypatch.setattr(Path, "write_text", capture_text)
    save_reports(tmp_path, (row for row in records), [], {})

    summary = json.loads(text_exports["campaign_summary.json"])
    assert summary["results"] == [records[0], records[2]]
    with (tmp_path / "campaign_scores.csv").open(
            encoding="utf-8-sig", newline="") as stream:
        assert list(csv.DictReader(stream)) == [
            {key: str(value) for key, value in row.items()} for row in records
        ]
    document = text_exports["campaign_report.html"]
    assert "<td>aggregate-first</td><td>2/4 (50.0%)</td>" in document
    assert "<td>aggregate-last</td><td>2/8 (25.0%)</td>" in document
    assert "regional-only" not in document
