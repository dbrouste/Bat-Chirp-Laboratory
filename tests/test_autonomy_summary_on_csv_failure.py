"""CSV write failures must preserve the previous campaign summary."""
import csv
from pathlib import Path

import pytest

from laboratory.local_campaign import save_reports


@pytest.mark.parametrize("stage", ["writeheader", "writerows"])
def test_csv_write_failure_preserves_summary(tmp_path, monkeypatch, stage):
    summary = tmp_path / "campaign_summary.json"
    summary.write_text("previous summary\n", encoding="utf-8")
    summary_stat = summary.stat()
    scores = tmp_path / "campaign_scores.csv"
    scores.write_bytes(b"previous CSV\r\n")
    files = set(tmp_path.iterdir())
    text_writes = []
    write_text = Path.write_text
    failure = OSError("CSV write failed")

    def track_write(path, text, **kwargs):
        text_writes.append(path)
        return write_text(path, text, **kwargs)

    def fail_write(*args, **kwargs):
        raise failure

    monkeypatch.setattr(Path, "write_text", track_write)
    monkeypatch.setattr(csv.DictWriter, stage, fail_write)

    with pytest.raises(OSError, match="CSV write failed") as caught:
        save_reports(tmp_path, [], [], {})

    assert caught.value is failure
    # Observe writes and metadata without reading the JSON file.
    assert text_writes == []
    assert summary.stat() == summary_stat
    assert scores.read_bytes() == b"previous CSV\r\n"
    assert set(tmp_path.iterdir()) == files
