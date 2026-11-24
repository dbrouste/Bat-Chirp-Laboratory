"""Missing CSV fields must fail before any campaign export is touched."""
from pathlib import Path
import tempfile

import pytest

from laboratory.local_campaign import save_reports


@pytest.mark.parametrize("missing_field", [
    "valid", "window", "threshold", "scenario", "region", "total",
    "baseline_flagged", "new_alerts", "legacy_new_alerts",
])
@pytest.mark.parametrize("region", ["middle", "all"])
@pytest.mark.parametrize("existing", [True, False])
def test_missing_field_rejected_before_export(tmp_path, monkeypatch,
                                             missing_field, region, existing):
    summary = tmp_path / "campaign_summary.json"
    scores = tmp_path / "campaign_scores.csv"
    document = tmp_path / "campaign_report.html"
    if existing:
        summary.write_text("previous summary\n", encoding="utf-8")
        scores.write_bytes(b"previous CSV\r\n")
        document.write_text("previous report\n", encoding="utf-8")
    files = set(tmp_path.iterdir())
    summary_stat = summary.stat() if existing else None

    def unexpected_export(*args, **kwargs):
        pytest.fail("invalid records must not write exports or create temporary files")

    monkeypatch.setattr(Path, "write_text", unexpected_export)
    monkeypatch.setattr(tempfile, "NamedTemporaryFile", unexpected_export)
    row = dict(window=3, threshold=1.5, scenario="sample", region="all",
               total=4, valid=4, baseline_flagged=1, new_alerts=2,
               legacy_new_alerts=1)
    incomplete = dict(row, region=region)
    del incomplete[missing_field]
    records = (item for item in [row, incomplete])

    with pytest.raises(KeyError) as caught:
        save_reports(tmp_path, records, [], {})

    assert caught.value.args == (missing_field,)
    assert set(tmp_path.iterdir()) == files
    if existing:
        # Check the summary without reading a JSON file.
        assert summary.stat() == summary_stat
        assert scores.read_bytes() == b"previous CSV\r\n"
        assert document.read_text(encoding="utf-8") == "previous report\n"
