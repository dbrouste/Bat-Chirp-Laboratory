"""Synthetic calibration anchors for the independent geometric score."""

import numpy as np
import pytest

from laboratory.geometry import (
    GeometryConfig, importance_weight, match_curves, recognised,
    score_pair, tolerance_khz,
)


MANUAL = [[0.0, 100.0], [4.0, 60.0]]


def test_manual_identical_automatic_is_perfect():
    result = score_pair(MANUAL, MANUAL, 40)
    assert result["score"] == pytest.approx(100, abs=1e-7)
    assert result["coverage"] == 1
    assert result["extension_penalty"] == 0
    assert result["main_slope_khz_per_ms"] == pytest.approx(-10)
    assert recognised(result)


def test_snr_tolerance_and_weight_are_bounded():
    assert [tolerance_khz(x) for x in (5, 15, 25, 35, 50)] == [5, 5, 4, 3, 3]
    assert [importance_weight(x) for x in (5, 15, 25, 35, 50)] == [1, 1, 2, 3, 3]


def test_vertical_frequency_offset_is_more_costly_at_high_snr():
    automatic = [[t, f + 2.5] for t, f in MANUAL]
    high = score_pair(MANUAL, automatic, 40)
    low = score_pair(MANUAL, automatic, 10)
    assert 0 < high["score"] < low["score"] < 100
    assert high["tolerance_perpendicular_khz"] == pytest.approx(3 / np.sqrt(2))


def test_half_chirp_coverage_and_score_are_penalized():
    first_half = [[0.0, 100.0], [2.0, 80.0]]
    result = score_pair(MANUAL, first_half, 40)
    assert result["coverage"] <= 0.55
    assert result["score"] <= 55.0


def test_straight_extension_is_allowed_but_echo_rebound_penalized():
    extension = [[0.0, 100.0], [6.0, 40.0]]
    echo = [[0.0, 100.0], [4.0, 60.0], [5.0, 80.0]]
    straight = score_pair(MANUAL, extension, 40)
    bent = score_pair(MANUAL, echo, 40)
    assert straight["score"] == pytest.approx(100, abs=1e-6)
    assert straight["end_extension"]["invalid_length"] == pytest.approx(0, abs=1e-6)
    assert bent["extension_penalty"] > 0
    assert bent["score"] < straight["score"]


def test_manual_knee_uses_local_normals():
    knee = [[0.0, 110.0], [2.0, 88.0], [3.0, 50.0], [5.0, 42.0]]
    result = score_pair(knee, knee, 35)
    assert result["score"] == pytest.approx(100, abs=1e-6)
    normals = np.array([p["normal"] for p in result["samples"]])
    assert np.std(normals[:, 1]) > 0.03


def test_low_slope_is_marked_degraded_but_finite():
    near_flat = [[0, 80], [4, 79.5]]
    result = score_pair(near_flat, near_flat, 20)
    assert result["normalization_degraded"]
    assert result["score"] == pytest.approx(100, abs=1e-6)


def test_one_to_one_matches_overlapping_harmonics():
    second = [[0.0, 60.0], [4.0, 20.0]]
    manual = [dict(id="fundamental", points=MANUAL, snr_max_db=40),
              dict(id="harmonic", points=second, snr_max_db=25)]
    auto = [dict(id="harmonic-auto", points=second),
            dict(id="fundamental-auto", points=MANUAL)]
    report = match_curves(manual, auto, exhaustive=True)
    assert report["weighted_recall"] == pytest.approx(1)
    assert report["weighted_trajectory_score"] == pytest.approx(1)
    assert [d["automatic_id"] for d in report["details"]] == [
        "fundamental-auto", "harmonic-auto"]
    assert report["false_positive_count"] == 0


def test_non_exhaustive_annotations_do_not_claim_false_positives():
    result = match_curves([], [{"id": 1, "points": MANUAL}], exhaustive=False)
    assert result["false_positive_count"] is None
    assert result["unmatched_automatic"][0]["status"] == "unassessed"


def test_no_auto_zero_score_and_no_detection():
    result = score_pair(MANUAL, None, 40)
    assert result["score"] == 0
    assert result["coverage"] == 0
    assert not recognised(result)


@pytest.mark.parametrize("invalid", [
    [[0, 90], [0, 80]],
    [[0, 90], [1, float("nan")]],
    [[0, 90]],
])
def test_invalid_curve_is_explicit(invalid):
    with pytest.raises(ValueError):
        score_pair(invalid, invalid, 40)
