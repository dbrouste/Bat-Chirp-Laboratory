"""Temporary-file close errors must not mask CSV write failures."""
import csv
import tempfile
from pathlib import Path

import pytest

from laboratory.local_campaign import save_reports


@pytest.fixture
def failing_close(monkeypatch):
    create_stream = tempfile.NamedTemporaryFile
    failure = OSError("close failed")
    attempts = []

    class FailingCloseStream:
        def __init__(self, *args, **kwargs):
            self.stream = create_stream(*args, **kwargs)

        def __getattr__(self, name):
            return getattr(self.stream, name)

        def close(self):
            attempts.append(Path(self.name))
            self.stream.close()
            raise failure

        def __enter__(self):
            return self

        def __exit__(self, *exc_info):
            self.close()

    monkeypatch.setattr(tempfile, "NamedTemporaryFile", FailingCloseStream)
    return failure, attempts


@pytest.mark.parametrize("stage", ["writeheader", "writerows"])
@pytest.mark.parametrize("failure_type", [OSError, KeyboardInterrupt])
def test_csv_write_failure_survives_close_failure(
        tmp_path, monkeypatch, failing_close, stage, failure_type):
    target = tmp_path / "campaign_scores.csv"
    target.write_bytes(b"previous export")
    failure = failure_type("CSV write failed")

    def fail_write(*args, **kwargs):
        raise failure

    monkeypatch.setattr(csv.DictWriter, stage, fail_write)
    with pytest.raises(failure_type) as caught:
        save_reports(tmp_path, [], [], {})

    assert caught.value is failure
    assert len(failing_close[1]) == 1
    assert not failing_close[1][0].exists()
    assert target.read_bytes() == b"previous export"
    assert set(tmp_path.iterdir()) == {target}


def test_close_failure_after_successful_write_is_reported(tmp_path, failing_close):
    target = tmp_path / "campaign_scores.csv"
    target.write_bytes(b"previous export")

    with pytest.raises(OSError) as caught:
        save_reports(tmp_path, [], [], {})

    assert caught.value is failing_close[0]
    assert len(failing_close[1]) == 1
    assert not failing_close[1][0].exists()
    assert target.read_bytes() == b"previous export"
    assert set(tmp_path.iterdir()) == {target}
