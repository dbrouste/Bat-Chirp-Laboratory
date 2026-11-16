"""Evidence-qualified failure categories and optional observer non-regression."""

from pathlib import Path
from types import SimpleNamespace
import sys

import numpy as np
import pytest

from laboratory import detection_probe as probe
from laboratory.failure_report import distribution, relationship


def source_guard_example(value):
    if value < 0:
        return None
    return value


def candidate(uid="raw:0:0", admissible=True, **values):
    return dict(uid=uid, admissible=admissible, t_start=1.0, t_end=2.0, f_low=100.0, f_high=200.0, **values)


@pytest.mark.parametrize("scenario,category,stage", [
    ("assignment", "matching_failure", "global_assignment"),
    ("nms", "known_rejection", "time_frequency_nms"),
    ("width", "known_rejection", "general_v4_width"),
    ("filter", "known_rejection", "branch_filters"),
    ("none", "no_candidate_observed", "spectral_blobs"),
    ("ambiguous", "indeterminate", "spectral_blobs"),
])
def test_only_observed_stages_support_failure_categories(scenario, category, stage):
    stages = {name: [] for name in ("general_raw", "general", "general_v4", "low_raw", "low_v4", "nms_input", "final")}
    raw = candidate(admissible=scenario != "ambiguous")
    if scenario != "none":
        stages["general_raw"] = [raw]
    if scenario in {"assignment", "nms", "width"}:
        stages["general"] = [raw]
    if scenario in {"assignment", "nms"}:
        stages["general_v4"] = stages["nms_input"] = [raw]
    if scenario == "assignment":
        stages["final"] = [raw]
    annotation = (1, np.array([1.0, 2.0]), np.array([200.0, 100.0]), None)
    matcher = lambda candidates, annotations, **kwargs: [(0, 0)] if candidates[0]["admissible"] else []
    row = probe.diagnose_missing(annotation, SimpleNamespace(stages=stages), matcher, "square_normals_v1")
    assert (row["category"], row["last_stage"]) == (category, stage)
    assert row["tracking_invoked_for_reference"] is False
    if scenario == "ambiguous":
        assert row["raw_bbox_overlap_count"] == 1 and row["raw_admissible_count"] == 0
    if scenario == "width":
        assert row["observed_rejections_by_uid"][raw["uid"]] == "general_v4_width"


def test_array_comparison_is_exact_and_excludes_only_profiling_clocks():
    first = {"curve": np.array([1.0, 2.0]), "diagnostics": {"timings_ms": {"total": 1}, "success": True}}
    second = {"curve": np.array([1.0, 2.0]), "diagnostics": {"timings_ms": {"total": 99}, "success": True}}
    assert probe.digest(first) == probe.digest(second)
    second["curve"][0] = np.nextafter(1.0, 2.0)
    assert probe.digest(first) != probe.digest(second)
    assert probe.digest(np.array([1.0], dtype="float32")) != probe.digest(np.array([1.0], dtype="float64"))


def test_return_guards_are_read_from_source_not_invented():
    guards = probe.return_guards(source_guard_example)
    assert list(guards.values()) == [["value < 0"]]


def test_observer_restores_trace_after_exception():
    observer = probe.ReturnObserver.__new__(probe.ReturnObserver)
    observer.functions = {}
    assert sys.gettrace() is None
    with pytest.raises(ValueError):
        with observer:
            raise ValueError("injected")
    assert sys.gettrace() is None


def test_observer_does_not_replace_an_existing_debugger():
    observer = probe.ReturnObserver.__new__(probe.ReturnObserver)
    previous = lambda frame, event, argument: None
    try:
        sys.settrace(previous)
        with pytest.raises(RuntimeError, match="debugger"):
            observer.__enter__()
        assert sys.gettrace() is previous
    finally:
        sys.settrace(None)


def test_signed_distributions_preserve_direction_and_missingness():
    values = distribution([-1.0, 0.0, 2.0, None])
    assert values["count"] == 3 and values["negative"] == values["zero"] == values["positive"] == 1
    assert values["mean_abs"] == 1.0 and values["median"] == 0.0
    assert distribution([]) == {"count": 0}
    assert relationship([{"snr": 1, "end": 2}, {"snr": 1, "end": 3}], "snr", "end")["pearson"] is None


def test_filter_explanation_retains_values_and_strict_operators():
    parameters = {"v4_general_min_width_ms": 1.33}
    explanation = probe.rejected_conditions({"width_ms": 1.0}, "general_v4_width", parameters)
    assert explanation == [{"field": "width_ms", "observed": 1.0, "required_operator": ">=", "threshold": 1.33}]
    assert parameters == {"v4_general_min_width_ms": 1.33}


def test_negative_pipeline_failure_is_unknown_not_zero_accepted():
    def invalid_group(*args, **kwargs):
        raise ValueError("invalid final curve")

    result, error = probe.group_outcome(SimpleNamespace(track_and_group_candidates=invalid_group), [], 1, [], {})
    assert result is None and error == {"type": "ValueError", "message": "invalid final curve"}


@pytest.mark.integration
@pytest.mark.skipif(not (Path(__file__).resolve().parents[2] / "bat_full_spectrum_processing" / "benchmark" / "reassignment_detector_seeds.py").is_file(),
                    reason="External processing source checkout is required")
def test_real_synthetic_pipeline_is_identical_with_optional_observation(tmp_path):
    from laboratory import campaign_control, runner
    from laboratory.frozen_dataset import collect_frozen_dataset

    dataset = tmp_path / "synthetic"
    campaign_control.synthetic_dataset(dataset)
    records, _ = collect_frozen_dataset(dataset)
    for name, record in records.items():
        result = runner._worker(name, record, str(dataset), dict(runner.DEFAULT_OPTIONS),
                                str(runner.DEFAULT_PROCESSING_ROOT), diagnostic_probe=True)
        assert "traceback" not in result, result.get("traceback")
        proof = result["diagnostics"]["equivalence"]
        assert proof["benchmark_numeric_and_decisions_exact"] is True
        assert proof["candidate_arrays_exact"] is True
        assert proof["matched_trajectory_arrays_exact"] is True
        if record.get("review_status") == "no_chirp":
            assert proof["negative_final_arrays_exact"] is True
