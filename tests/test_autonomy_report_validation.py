"""Invalid CSV fields must be rejected before any report is written."""
from pathlib import Path

import pytest

from laboratory.local_campaign import save_reports


@pytest.mark.parametrize("region", ["all", "middle"])
def test_invalid_fields_preserve_existing_reports(tmp_path, monkeypatch, region):
    summary = tmp_path / "campaign_summary.json"
    scores = tmp_path / "campaign_scores.csv"
    document = tmp_path / "campaign_report.html"
    summary.write_text("previous summary\n", encoding="utf-8")
    scores.write_bytes(b"previous CSV\r\n")
    document.write_text("previous report\n", encoding="utf-8")
    summary_stat = summary.stat()
    files = set(tmp_path.iterdir())
    text_writes = []
    write_text = Path.write_text

    def track_write(path, text, **kwargs):
        text_writes.append(path)
        return write_text(path, text, **kwargs)

    monkeypatch.setattr(Path, "write_text", track_write)
    row = dict(window=3, threshold=1.5, scenario="sample", region="all",
               total=4, valid=4, baseline_flagged=1, new_alerts=2,
               legacy_new_alerts=1)
    records = (item for item in [row, dict(row, region=region, unexpected=1)])

    with pytest.raises(ValueError, match="fields not in fieldnames.*unexpected"):
        save_reports(tmp_path, records, [], {})

    # Observe writes and metadata without reading the JSON file.
    assert text_writes == []
    assert summary.stat() == summary_stat
    assert scores.read_bytes() == b"previous CSV\r\n"
    assert document.read_text(encoding="utf-8") == "previous report\n"
    assert set(tmp_path.iterdir()) == files
