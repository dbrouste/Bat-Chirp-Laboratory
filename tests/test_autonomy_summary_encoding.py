"""Summary encoding failures must preserve all campaign exports."""
from pathlib import Path

import pytest

from laboratory.local_campaign import save_reports


@pytest.mark.parametrize("existing", [False, True])
@pytest.mark.parametrize("surrogate", ["\ud800", "\udfff"])
@pytest.mark.parametrize("field", ["definition", "runs"])
def test_summary_encoding_failure_preserves_exports(
    tmp_path, monkeypatch, existing, surrogate, field
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
    definition = {"name": surrogate} if field == "definition" else {}
    runs = [surrogate] if field == "runs" else []

    with pytest.raises(UnicodeEncodeError):
        save_reports(tmp_path, [], runs, definition)

    assert set(tmp_path.iterdir()) == set(before)
    assert text_writes == []
    # Check preservation without reading JSON.
    assert {path: path.stat() for path in before} == before
    if existing:
        assert scores.read_bytes() == b"previous CSV\r\n"
        assert report.read_bytes() == b"previous HTML\n"


def test_valid_unicode_summary_exports(tmp_path, monkeypatch):
    summary_texts = []
    write_text = Path.write_text

    def track_write(path, text, **kwargs):
        if path.name == "campaign_summary.json":
            summary_texts.append(text)
        return write_text(path, text, **kwargs)

    monkeypatch.setattr(Path, "write_text", track_write)
    save_reports(tmp_path, [], [], {"name": "campagne \u00e9t\u00e9 \U0001f987"})

    assert len(summary_texts) == 1
    assert "campagne \u00e9t\u00e9 \U0001f987" in summary_texts[0]
    assert {path.name for path in tmp_path.iterdir()} == {
        "campaign_scores.csv", "campaign_summary.json", "campaign_report.html"
    }
