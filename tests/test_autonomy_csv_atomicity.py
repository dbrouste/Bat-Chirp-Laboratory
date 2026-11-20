"""Failed CSV exports preserve the last complete campaign scores."""
import csv
from pathlib import Path

import pytest

from laboratory.local_campaign import save_reports


@pytest.fixture
def records():
    return [dict(window=3, threshold=1.5, scenario="sample", region="all",
                 total=4, valid=4, baseline_flagged=1, new_alerts=2,
                 legacy_new_alerts=1)]


@pytest.mark.parametrize("existing", [False, True])
def test_invalid_row_preserves_csv(tmp_path, records, existing):
    target = tmp_path / "campaign_scores.csv"
    if existing:
        save_reports(tmp_path, records, [], {})
    before = target.read_bytes() if existing else None
    files = set(tmp_path.iterdir())

    with pytest.raises(ValueError, match="fields not in fieldnames"):
        save_reports(tmp_path, [dict(records[0], scenario="replacement"),
                                dict(records[0], unexpected=1)], [], {})

    assert (target.read_bytes() if target.exists() else None) == before
    assert set(tmp_path.iterdir()) - files <= {tmp_path / "campaign_summary.json"}


@pytest.mark.parametrize("existing", [False, True])
def test_replace_failure_preserves_csv(tmp_path, monkeypatch, records, existing):
    target = tmp_path / "campaign_scores.csv"
    if existing:
        save_reports(tmp_path, records, [], {})
    before = target.read_bytes() if existing else None
    files = set(tmp_path.iterdir())

    def fail_replace(source, destination):
        assert source.parent == target.parent
        assert destination == target
        raise OSError("replacement failed")

    monkeypatch.setattr(Path, "replace", fail_replace)
    with pytest.raises(OSError, match="replacement failed"):
        save_reports(tmp_path, records, [], {})

    assert (target.read_bytes() if target.exists() else None) == before
    assert set(tmp_path.iterdir()) - files <= {tmp_path / "campaign_summary.json"}


def test_success_replaces_csv_with_complete_export(tmp_path, records):
    target = tmp_path / "campaign_scores.csv"
    target.write_bytes(b"previous export")

    save_reports(tmp_path, records, [], {})

    assert target.read_bytes().startswith(b"\xef\xbb\xbf")
    with target.open(encoding="utf-8-sig", newline="") as stream:
        assert list(csv.DictReader(stream)) == [
            {key: str(value) for key, value in records[0].items()}
        ]
    assert {path.name for path in tmp_path.iterdir()} == {
        "campaign_scores.csv", "campaign_summary.json", "campaign_report.html"
    }
