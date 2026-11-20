"""HTML rendering failures must preserve all campaign exports."""
from pathlib import Path

import pytest

from laboratory.local_campaign import save_reports


@pytest.mark.parametrize("existing", [False, True])
def test_missing_valid_preserves_exports(tmp_path, monkeypatch, existing):
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
    row = dict(window=3, threshold=1.5, scenario="sample", region="all",
               total=4, valid=4, baseline_flagged=1, new_alerts=2,
               legacy_new_alerts=1)
    malformed = dict(row)
    del malformed["valid"]

    with pytest.raises(KeyError) as caught:
        save_reports(tmp_path, [row, malformed], [], {})

    assert caught.value.args == ("valid",)
    assert set(tmp_path.iterdir()) == set(before)
    assert text_writes == []
    # Verify summary metadata without reading JSON.
    assert {path: path.stat() for path in before} == before
    if existing:
        assert scores.read_bytes() == b"previous CSV\r\n"
        assert report.read_bytes() == b"previous HTML\n"
