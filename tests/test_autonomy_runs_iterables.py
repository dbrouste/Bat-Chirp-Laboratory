"""Run iterables must produce the same campaign exports as lists."""
import json
from pathlib import Path

import pytest

from laboratory.local_campaign import save_reports


@pytest.mark.parametrize("runs", [[], [
    {"run_id": "first", "window_points": 3, "state": "completed"},
    {"run_id": "second", "window_points": 5, "state": "completed"},
]])
def test_generator_runs_match_list_exports(tmp_path, monkeypatch, runs):
    writes = []

    def capture_write(path, text, **kwargs):
        writes.append((path.name, text, kwargs))
        return len(text)

    monkeypatch.setattr(Path, "write_text", capture_write)
    save_reports(tmp_path, [], runs, {})
    list_writes = writes[:]
    scores = (tmp_path / "campaign_scores.csv").read_bytes()
    writes.clear()

    save_reports(tmp_path, [], (run for run in runs), {})

    assert writes == list_writes
    assert [(name, kwargs) for name, _, kwargs in writes] == [
        ("campaign_summary.json", {"encoding": "utf-8"}),
        ("campaign_report.html", {"encoding": "utf-8"}),
    ]
    # Inspect captured text only; do not read the JSON file.
    assert json.loads(writes[0][1])["runs"] == runs
    assert writes[0][1].endswith("\n")
    assert (tmp_path / "campaign_scores.csv").read_bytes() == scores
