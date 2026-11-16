"""Campaign durability tests using synthetic inputs and an instrumented runner."""

import json
from pathlib import Path

import pytest

from laboratory import campaign_control as control, runner
from laboratory.aggregation import summarize_results


class FakeJob:
    def __init__(self, memory_bytes):
        self.memory_bytes = memory_bytes

    def __enter__(self):
        return self

    def __exit__(self, *args):
        pass


@pytest.fixture
def campaign(tmp_path, monkeypatch):
    dataset = tmp_path / "synthetic-input"
    control.synthetic_dataset(dataset)
    processing = tmp_path / "processing"
    (processing / "bat_analysis").mkdir(parents=True)
    (processing / "benchmark").mkdir()
    (processing / "bat_analysis" / "detection.py").write_text("value = 1\n", encoding="utf-8")
    monkeypatch.setattr(control, "WindowsJob", FakeJob)
    folder = tmp_path / "campaign"
    calls = []
    behavior = {}

    def fake_runner(**kwargs):
        definition = control.load_definition(folder)
        calls.append(kwargs["options"])
        detail = dict(file="calibration/wav/synthetic/positive.wav", chirp_id=1, snr_max_db=40,
                      detection_importance_weight=1, detector_matched=True, tracking_success=True,
                      within_snr_tolerance_fraction=0.9, mean_full_interval_error_khz=0.5,
                      mean_normalized_error=0.2, coverage_fraction=1, start_error_ms=0, end_error_ms=0,
                      mean_geometric_error_khz_eq=0.3, geometric_coverage_fraction=1)
        per_file = [dict(file=detail["file"], annotated=1, candidates=1, matched=1, tracking_succeeded=1),
                    dict(file="calibration/wav/synthetic/negative.wav", annotated=0, candidates=0,
                         matched=0, tracking_succeeded=0, reference_class="reviewed_no_chirp")]
        totals = summarize_results(per_file, [detail])
        totals["reviewed_no_chirp"] = dict(wav_count=1, wavs_with_candidate_alert=0, candidate_alerts=0,
                                           wav_false_alarm_fraction=0)
        report = {"metadata": {"status": "complete", "code_fingerprint": definition["code_fingerprint"],
            "options": kwargs["options"], "input_fingerprints": definition["identity"]["inputs"],
            "frozen_dataset": definition["identity"]["provenance"]}, "totals": totals,
            "per_file": per_file, "details": [detail]}
        runner._write_json_atomic(kwargs["out"] / "report.json", report)
        return report

    def fake_bounded(command, **kwargs):
        job_id = command[command.index("--job-id") + 1]
        attempt_id = command[command.index("--attempt") + 1]
        kwargs["gate"].write_text("assigned", encoding="utf-8")
        kwargs["log"].write_text("synthetic runner\n", encoding="utf-8")
        if behavior.pop("interrupt", False) and job_id != "__verify__":
            return {"status": "interrupted", "error": "injected stop", "elapsed_s": 0.01}
        try:
            control.execute_worker(folder, job_id, attempt_id)
            return {"status": "complete", "error": None, "elapsed_s": 0.01, "peak_job_commit_bytes": 100}
        except Exception as exc:
            return {"status": "failed", "error": str(exc), "elapsed_s": 0.01}

    monkeypatch.setattr(runner, "run", fake_runner)
    monkeypatch.setattr(control, "run_bounded", fake_bounded)
    return folder, dataset, processing, calls, behavior


def prepare(campaign, **kwargs):
    folder, dataset, processing, _, _ = campaign
    return control.prepare(folder, dataset, processing, variants=[
        {"name": "width", "options": {"v4_general_min_width_ms": 1.0}}], min_free_gb=0, **kwargs)


def test_pause_resume_and_fixed_baseline_do_not_recompute_valid_results(campaign):
    folder, _, _, calls, _ = campaign
    prepare(campaign)
    first = control.run_campaign(folder, max_new=1)
    assert first["status"] == "paused"
    assert first["progress"] == {"complete": 1, "total": 2}
    baseline = first["baseline_report_sha256"]
    second = control.run_campaign(folder, resume=True)
    assert second["status"] == "complete"
    assert second["baseline_report_sha256"] == baseline
    assert len(calls) == 2
    third = control.run_campaign(folder, resume=True)
    assert third["status"] == "complete" and len(calls) == 2
    assert all(job["result"]["false_final_trajectories"] is None for job in third["jobs"])
    assert all(job["result"]["baseline_identity"] == third["baseline_identity"] for job in third["jobs"])
    assert (folder / "campaign_scores.csv").is_file()
    assert (folder / "campaign_report.html").is_file()


def test_interrupted_attempt_resumes_without_using_incomplete_result(campaign):
    folder, _, _, calls, _ = campaign
    prepare(campaign)
    original = control.run_bounded
    stopped = []

    def interrupt_experiment(command, **kwargs):
        if command[command.index("--job-id") + 1] != "__verify__" and not stopped:
            stopped.append(True)
            return {"status": "interrupted", "error": "injected stop", "elapsed_s": 0.01}
        return original(command, **kwargs)

    control.run_bounded = interrupt_experiment
    try:
        first = control.run_campaign(folder)
        assert first["status"] == "interrupted"
        assert first["progress"]["complete"] == 0
        resumed = control.run_campaign(folder, resume=True)
        assert resumed["status"] == "complete" and len(calls) == 2
        assert any(attempt["status"] == "interrupted" for attempt in resumed["attempts"])
    finally:
        control.run_bounded = original


def test_baseline_tampering_is_an_explicit_error(campaign):
    folder, _, _, calls, _ = campaign
    prepare(campaign)
    first = control.run_campaign(folder, max_new=1)
    path = control.benchmark_path(folder, first["jobs"][0]["job_id"])
    path.write_bytes(path.read_bytes() + b" ")
    assert control.export_reports(folder)["status"] == "integrity_error"
    resumed = control.run_campaign(folder, resume=True)
    assert resumed["status"] == "integrity_error"
    assert "Fixed baseline" in resumed["error"]
    assert len(calls) == 1


@pytest.mark.parametrize("change", ["code", "audio", "annotations", "definition"])
def test_changed_provenance_is_not_reused(campaign, change):
    folder, dataset, processing, calls, _ = campaign
    prepare(campaign)
    if change == "code":
        path = processing / "bat_analysis" / "detection.py"
    elif change == "audio":
        path = dataset / "calibration" / "wav" / "synthetic" / "positive.wav"
    elif change == "annotations":
        path = dataset / "calibration" / "annotations" / "synthetic" / "synthetic.json"
    else:
        path = folder / "definition.json"
    if change == "definition":
        definition = json.loads(path.read_text(encoding="utf-8"))
        definition["limits"]["workers"] = 8
        path.write_text(json.dumps(definition), encoding="utf-8")
        with pytest.raises(ValueError, match="integrity"):
            control.run_campaign(folder)
    else:
        path.write_bytes(path.read_bytes() + b"changed")
        assert control.run_campaign(folder)["status"] == "failed"
    assert calls == []


def test_crash_reservation_is_charged_and_attempt_limit_is_enforced(campaign):
    folder, _, _, _, _ = campaign
    prepare(campaign, campaign_seconds=1, experiment_seconds=1, max_attempts=1)
    definition = control.load_definition(folder)
    baseline = definition["experiments"][0]["job_id"]
    with control.database(folder) as connection:
        connection.execute("INSERT INTO detector_attempts VALUES('crash',?,'past',NULL,1,NULL,'running',NULL,'old.log')", (baseline,))
        connection.execute("UPDATE detector_jobs SET status='running' WHERE job_id=?", (baseline,))
    report = control.run_campaign(folder, resume=True)
    assert report["status"] == "failed"
    assert "budget exhausted" in report["error"]
    assert report["execution_seconds_charged"] == 1
    assert report["attempts"][0]["status"] == "interrupted"


def test_stop_request_is_acknowledged_only_by_resume(campaign):
    folder, _, _, calls, _ = campaign
    prepare(campaign)
    assert control.main(["stop", "--campaign", str(folder)]) == 0
    report = control.run_campaign(folder)
    assert report["status"] == "interrupted" and calls == []
    assert control.run_campaign(folder, resume=True)["status"] == "complete"


@pytest.mark.parametrize("limits", [
    {"workers": 9}, {"memory_gb": float("nan")}, {"campaign_seconds": 0},
    {"max_output_gb": 1}, {"max_experiments": 1},
])
def test_unsupported_limits_fail_before_campaign_creation(campaign, limits):
    folder, _, _, _, _ = campaign
    with pytest.raises(ValueError):
        prepare(campaign, **limits)
    assert not folder.exists()


def test_scoring_parameters_are_not_experimental_variables(campaign):
    with pytest.raises(ValueError, match="scoring"):
        control.options_with({"geometry_mode": "vertical_legacy"})


@pytest.mark.parametrize("payload", [
    {"per_file": [], "details": []},
    {"per_file": [{"error": "incomplete"}], "details": []},
    {"per_file": [{}], "details": [], "traceback": "failed"},
])
def test_incomplete_wav_cache_is_not_reused(tmp_path, payload):
    path = tmp_path / "cache.json"
    path.write_text(json.dumps(payload), encoding="utf-8")
    assert runner._read_cache(path) is None


def test_status_identifies_a_missing_supervisor_without_changing_history(campaign):
    folder, _, _, _, _ = campaign
    prepare(campaign)
    with control.database(folder) as connection:
        connection.execute("UPDATE detector_control SET status='running'")
    assert control.export_reports(folder)["status"] == "interrupted_owner_missing"
    with control.database(folder) as connection:
        assert connection.execute("SELECT status FROM detector_control").fetchone()[0] == "running"


def test_pilot_report_reuses_metrics_and_requires_global_memory_proof(campaign, tmp_path):
    import csv
    from laboratory import pilot_report, pilot_selection
    from laboratory.dataset import sha256_file, stable_hash

    folder, dataset, processing, _, _ = campaign
    with (dataset / "manifest.csv").open(encoding="utf-8", newline="") as stream:
        assignments = [{**row, "cohort": "development"} for row in csv.DictReader(stream)]
    snapshot = {"schema": "chirp_cohort_snapshot_v1", "manifest_sha256": sha256_file(dataset / "manifest.csv"),
                "assignments": assignments}
    snapshot["snapshot_id"] = stable_hash(snapshot)
    snapshot_path = tmp_path / "snapshot.json"
    snapshot_path.write_text(json.dumps(snapshot), encoding="utf-8")
    selection_dir = tmp_path / "selection"
    pilot_selection.prepare_selection(dataset, snapshot_path, selection_dir)
    control.prepare(folder, dataset, processing, selection_file=selection_dir / "selection.json", global_memory=True, memory_gb=4)
    assert control.run_campaign(folder)["status"] == "complete"
    with pytest.raises(ValueError, match="memory enforcement"):
        pilot_report.write_report(folder)
    directory = folder / "global_runs" / "test"
    directory.mkdir(parents=True)
    (directory / "reservation.json").write_text('{"reserved_s":60}', encoding="utf-8")
    (directory / "outcome.json").write_text(json.dumps({"elapsed_s":1, "status":"complete",
        "scope":"supervisor_and_all_descendant_workers_commit_memory", "peak_job_commit_bytes":100,
        "memory_limit_bytes":4*1024**3, "job_memory_limit_verified":True, "process_tree_assigned":True}), encoding="utf-8")
    result = pilot_report.write_report(folder)
    assert result["scientific_results"]["matched"] == 1
    assert result["final_false_trajectories"] is None
    assert (folder / "pilot_metrics.csv").is_file() and (folder / "pilot_report.html").is_file()
