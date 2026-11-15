"""The local optimizer must be resumable, bounded and calibration-only."""
import json
from pathlib import Path

import pytest

from laboratory import autonomous_optimizer as agent


def _metric(*, recall=.81, high=.93, weighted=.85, quality=.75,
            geometry=1.17, candidates=1154):
    return {
        "annotated": 493, "recall": recall, "weighted_recall": weighted,
        "full_pipeline": quality - .03, "weighted_full_pipeline": quality,
        "high_snr_count": 360, "high_snr_recall": high,
        "geometry_khz_eq": geometry, "coverage": .95,
        "candidates": candidates,
    }


def _report(options):
    width = options.get("v4_general_min_width_ms")
    quality = .75 if width is None else (.77 if width == 1.0 else .73)
    weighted = .85 if width is None else (.87 if width == 1.0 else .84)
    recall = .81 if width is None else (.83 if width == 1.0 else .80)
    high = .93 if width is None else (.94 if width == 1.0 else .88)
    return {
        "metadata": {"status": "complete"},
        "totals": {
            "annotated": 493,
            "matched": round(493 * recall),
            "weighted_detector_recall": weighted,
            "full_pipeline_within_tolerance_fraction": quality - .03,
            "weighted_full_pipeline_within_tolerance_fraction": quality,
            "mean_geometric_error_khz_eq": 1.17,
            "mean_geometric_coverage_fraction": .95,
            "candidates": 1154,
            "by_snr": {"high_35_db_or_more": {
                "annotated": 360, "detector_recall": high}},
        },
        "details": [{
            "file": "calibration/wav/one.wav", "chirp_id": 7,
            "detector_matched": False, "snr_max_db": 51,
        }, {
            "file": "calibration/wav/one.wav", "chirp_id": 8,
            "detector_matched": True, "snr_max_db": 38,
            "mean_geometric_error_khz_eq": 4.5,
            "geometric_coverage_fraction": .6,
            "start_error_ms": .7, "end_error_ms": 2,
        }],
    }


def _setup(tmp_path, monkeypatch):
    dataset = tmp_path / "dataset-v1"
    dataset.mkdir()
    processing = tmp_path / "processing"
    (processing / "benchmark").mkdir(parents=True)
    (processing / "benchmark" / "reassignment_detector_seeds.py").write_text(
        "def evaluate(*args, geometry_mode=None): pass\n")
    partitions = []
    calls = []

    def fake_collect(root, *, partition):
        partitions.append(partition)
        return {}, {"dataset": str(root), "partition": partition,
                    "manifest_sha256": "frozen-a", "wav_count": 37,
                    "hashes_verified": True}

    def fake_runner(**kwargs):
        calls.append(kwargs)
        result = _report(kwargs["options"])
        out = kwargs["out"]
        out.mkdir(parents=True, exist_ok=True)
        (out / "report.json").write_text(
            json.dumps(result), encoding="utf-8")
        return result

    monkeypatch.setattr(agent, "collect_frozen_dataset", fake_collect)
    monkeypatch.setattr(agent.runner, "code_fingerprint",
                        lambda _root: "detector-commit-abc")
    monkeypatch.setattr(agent.runner, "run", fake_runner)
    return dataset, processing, partitions, calls


def test_judge_rejects_high_snr_regression():
    base = _metric()
    candidate = _metric(high=.89, weighted=.91, quality=.82)
    eligible, reason, score = agent.judge(candidate, base)
    assert not eligible
    assert "high SNR" in reason
    assert score == -1


def test_judge_does_not_call_unmatched_candidates_false_positives():
    base = _metric()
    candidate = _metric(weighted=.87, quality=.77, candidates=1300)
    eligible, reason, score = agent.judge(candidate, base)
    assert eligible
    assert score > 0
    assert reason == "valid"


def test_unmatched_candidate_count_does_not_change_quality_score_or_eligibility():
    # Even WAVs with manual chirps can contain many real, unannotated calls.
    # Different candidate counts are diagnostic only, never labelled false positives.
    base = _metric()
    fewer = _metric(weighted=.87, quality=.77, candidates=200)
    more = _metric(weighted=.87, quality=.77, candidates=5000)
    e1, _, score1 = agent.judge(fewer, base)
    e2, _, score2 = agent.judge(more, base)
    assert e1 and e2
    assert score1 == score2


def test_candidate_count_alone_cannot_be_an_optimization():
    base = _metric()
    identical_with_less_candidates = dict(base, candidates=10)
    e1, _, score_base = agent.judge(base, base)
    e2, _, score_less = agent.judge(identical_with_less_candidates, base)
    assert e1 and e2
    assert score_base == score_less


def test_agent_autoresumes_with_no_repeated_trials(tmp_path, monkeypatch):
    dataset, processing, partitions, calls = _setup(tmp_path, monkeypatch)
    out = tmp_path / "results"

    partial = agent.run_agent(dataset=dataset, out=out,
                              processing_root=processing, max_new=2)
    assert partial["new_trials_this_invocation"] == 2
    assert partial["trials_total"] == 2
    assert calls[0]["options"] == agent.DEFAULTS
    assert calls[1]["options"]["v4_general_min_width_ms"] == 1.0
    assert all(p == "calibration" for p in partitions)
    assert all(c["max_files"] is None if "max_files" in c else True
               for c in calls)
    assert (out / "agent_report.html").is_file()
    assert (out / "priority_cases.csv").is_file()

    resumed = agent.run_agent(dataset=dataset, out=out,
                              processing_root=processing, max_total=4)
    assert resumed["trials_total"] == 4
    assert resumed["new_trials_this_invocation"] == 2
    ids = [trial["id"] for trial in
           json.loads((out / "agent_state.json").read_text())["trials"]]
    assert len(set(ids)) == 4
    assert resumed["recommended"]["v4_general_min_width_ms"] == 1.0
    assert len(calls) == 4
    assert all(p == "calibration" for p in partitions)

    final = agent.run_agent(dataset=dataset, out=out,
                            processing_root=processing, max_total=4)
    assert final["new_trials_this_invocation"] == 0
    assert len(calls) == 4


def test_state_provenance_change_blocks_resume(tmp_path, monkeypatch):
    dataset, processing, _, calls = _setup(tmp_path, monkeypatch)
    out = tmp_path / "results"
    agent.run_agent(dataset=dataset, out=out,
                    processing_root=processing, max_new=1)
    monkeypatch.setattr(agent.runner, "code_fingerprint",
                        lambda _root: "different-code")
    with pytest.raises(ValueError, match="no longer matches"):
        agent.run_agent(dataset=dataset, out=out,
                        processing_root=processing, max_new=1)
    assert len(calls) == 1
    assert not (out / ".agent.lock").exists()


def test_input_guardrails(tmp_path, monkeypatch):
    dataset, processing, _, calls = _setup(tmp_path, monkeypatch)
    with pytest.raises(ValueError, match="outside"):
        agent.run_agent(dataset=dataset, out=dataset / "results",
                        processing_root=processing)
    with pytest.raises(ValueError, match="max_total"):
        agent.run_agent(dataset=dataset, out=tmp_path / "results",
                        processing_root=processing, max_total=50)
    assert calls == []


def test_case_priority_favors_missed_high_snr():
    problems = agent._cases(_report(agent.DEFAULTS))
    assert len(problems) == 2
    assert problems[0]["priority"] == 100
    assert problems[0]["issues"] == "not_detected"
    assert "geometry_above_3_khz_eq" in problems[1]["issues"]
    assert "end_error_above_1.5_ms" in problems[1]["issues"]


def test_baseline_failure_stops_with_failed_trial(tmp_path, monkeypatch):
    dataset, processing, _, calls = _setup(tmp_path, monkeypatch)

    def broken(**kwargs):
        raise RuntimeError("simulated detector crash")

    monkeypatch.setattr(agent.runner, "run", broken)
    result = agent.run_agent(dataset=dataset, out=tmp_path / "results",
                             processing_root=processing)
    assert result["trials_complete"] == 0
    assert result["trials_total"] == 1
    state = json.loads((tmp_path / "results" / "agent_state.json").read_text())
    assert state["trials"][0]["status"] == "failed"
