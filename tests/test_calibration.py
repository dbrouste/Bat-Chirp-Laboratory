"""Calibration protocol and anti-leakage guardrails."""

import json

import pytest

from laboratory.calibration import calibrate, read_examples


MANUAL = [[0, 90], [1, 80], [2, 70], [3, 60]]


def _example(index, group, split, good, snr=40):
    return {"id": str(index), "group": group, "split": split,
            "expected_good": good, "snr_max_db": snr,
            "manual_points": MANUAL,
            "automatic_points": MANUAL if good else
            [[0, 120], [1, 110], [2, 100], [3, 90]]}


def test_supervised_calibration_seals_test_and_keeps_validation_out_of_search(tmp_path):
    examples = [_example(1, "night-A", "development", True),
                _example(2, "night-A", "development", False),
                _example(3, "night-B", "validation", True),
                _example(4, "night-B", "validation", False),
                {"id": "5", "group": "night-C", "split": "test"}]
    path = tmp_path / "examples.json"
    path.write_text(json.dumps({"examples": examples}), encoding="utf-8")
    chosen = calibrate(read_examples(path))
    assert chosen["tested_combinations"] == 324
    assert chosen["development"]["weighted_f1"] == pytest.approx(1)
    assert chosen["validation"]["weighted_f1"] == pytest.approx(1)
    assert chosen["sealed_test_examples"] == 1
    assert 0 < chosen["config"]["min_match_score"] < 100


def test_calibration_rejects_same_session_in_multiple_splits(tmp_path):
    path = tmp_path / "examples.json"
    path.write_text(json.dumps({"examples": [
        _example(1, "same-night", "development", True),
        _example(2, "same-night", "validation", False)]}), encoding="utf-8")
    with pytest.raises(ValueError, match="leaks across splits"):
        read_examples(path)


def test_calibration_requires_positive_and_negative_development_examples():
    with pytest.raises(ValueError, match="negative"):
        calibrate([_example(1, "night-A", "development", True)])
