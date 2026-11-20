"""Campaign labels survive Windows text-mode newline translation."""

import csv
from pathlib import Path

import pytest

from laboratory.local_campaign import save_reports


@pytest.mark.parametrize("scenario", [
    "first <variant>\r\n\r\n\tlast & final",
    "first <variant>\n\n\tlast & final",
    "first <variant>\r\n\n\tlast & final",
])
def test_windows_newline_translation_preserves_campaign_labels(
        tmp_path, monkeypatch, scenario):
    row = dict(window=3, threshold=1.5, scenario=scenario, region="all",
               total=4, valid=4, baseline_flagged=1, new_alerts=2,
               legacy_new_alerts=1)
    original = row.copy()

    def windows_write_text(path, text, *, encoding):
        # Exercise real Windows text-mode translation on every platform.
        with path.open("w", encoding=encoding, newline="\r\n") as stream:
            return stream.write(text)

    monkeypatch.setattr(Path, "write_text", windows_write_text)

    save_reports(tmp_path, [row], [], {})

    written = (tmp_path / "campaign_report.html").read_bytes()
    assert b"\r\r\n" not in written
    assert (
        b"<td>first &lt;variant&gt;\r\n\r\n\tlast &amp; final</td>"
    ) in written
    assert row == original
    with (tmp_path / "campaign_scores.csv").open(
            encoding="utf-8-sig", newline="") as stream:
        assert next(csv.DictReader(stream))["scenario"] == scenario
