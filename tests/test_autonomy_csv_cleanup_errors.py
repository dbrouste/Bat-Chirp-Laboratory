"""CSV cleanup failures must not mask the original export failure."""
import csv
from pathlib import Path

import pytest

from laboratory.local_campaign import save_reports


@pytest.mark.parametrize("stage", ["writeheader", "writerows", "replace"])
@pytest.mark.parametrize("failure_type", [OSError, KeyboardInterrupt])
def test_export_failure_survives_cleanup_failure(tmp_path, monkeypatch, stage,
                                               failure_type):
    target = tmp_path / "campaign_scores.csv"
    target.write_bytes(b"previous export")
    failure = failure_type("export failed")
    cleanup_attempts = []

    def fail_export(*args, **kwargs):
        raise failure

    def fail_cleanup(path, *, missing_ok=False):
        assert missing_ok
        assert path.parent == tmp_path
        assert path.name.startswith(".campaign_scores.")
        cleanup_attempts.append(path)
        raise PermissionError("cleanup failed")

    owner = Path if stage == "replace" else csv.DictWriter
    monkeypatch.setattr(owner, stage, fail_export)
    monkeypatch.setattr(Path, "unlink", fail_cleanup)

    with pytest.raises(failure_type) as caught:
        save_reports(tmp_path, [], [], {})

    assert caught.value is failure
    assert len(cleanup_attempts) == 1
    assert cleanup_attempts[0].is_file()
    assert target.read_bytes() == b"previous export"


def test_cleanup_failure_after_successful_export_is_reported(tmp_path, monkeypatch):
    failure = PermissionError("cleanup failed")

    def fail_cleanup(path, *, missing_ok=False):
        raise failure

    monkeypatch.setattr(Path, "unlink", fail_cleanup)
    with pytest.raises(PermissionError) as caught:
        save_reports(tmp_path, [], [], {})

    assert caught.value is failure
    assert (tmp_path / "campaign_scores.csv").is_file()
