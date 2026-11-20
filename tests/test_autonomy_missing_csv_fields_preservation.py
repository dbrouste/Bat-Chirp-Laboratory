"""Incomplete regional rows must preserve previously successful exports."""
import tempfile

import pytest

from laboratory.local_campaign import save_reports


@pytest.mark.parametrize("regional_first", [False, True])
def test_missing_regional_valid_preserves_successful_exports(
        tmp_path, monkeypatch, regional_first):
    aggregate = dict(window=3, threshold=1.5, scenario="sample", region="all",
                     total=4, valid=4, baseline_flagged=1, new_alerts=2,
                     legacy_new_alerts=1)
    regional = dict(aggregate, region="middle")
    save_reports(tmp_path, [aggregate, regional], [], {})
    before = {path: path.stat() for path in tmp_path.iterdir()}
    scores = tmp_path / "campaign_scores.csv"
    report = tmp_path / "campaign_report.html"
    scores_before = scores.read_bytes()
    report_before = report.read_bytes()

    def unexpected_temporary_file(*args, **kwargs):
        pytest.fail("missing fields must be rejected before creating temporary files")

    monkeypatch.setattr(tempfile, "NamedTemporaryFile", unexpected_temporary_file)
    del regional["valid"]
    aggregate["scenario"] = "replacement"
    rows = [regional, aggregate] if regional_first else [aggregate, regional]

    with pytest.raises(KeyError) as caught:
        save_reports(tmp_path, iter(rows), [], {})

    assert caught.value.args == ("valid",)
    # Include summary metadata without reading the JSON export.
    assert {path: path.stat() for path in tmp_path.iterdir()} == before
    assert scores.read_bytes() == scores_before
    assert report.read_bytes() == report_before
