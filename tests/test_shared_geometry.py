"""Regression tests for the canonical geometry used by both repositories."""
import numpy as np
import pytest
from chirp_geometry import (
    PROTOCOL, compare_normal_curves, reference_normals, square_time_scale,
)


@pytest.mark.parametrize("slope", [0.0, -1.0, -2.0, -10.0, -25.0, -100.0, 20.0])
def test_exact_normal_offset_is_slope_independent(slope):
    manual_t = np.array([20.0, 24.0])
    manual_f = 120.0 + slope * (manual_t - 20.0)
    samples = np.linspace(20, 24, 21)
    normals = reference_normals(manual_t, manual_f, samples)
    scale = normals["time_scale_khz_per_ms"]
    offset = 3.0
    nu, nf = normals["normal_u"][0], normals["normal_f"][0]
    auto_t = manual_t + offset * nu / scale
    auto_f = manual_f + offset * nf
    result = compare_normal_curves(manual_t, manual_f, auto_t, auto_f, samples)
    assert result["protocol"] == PROTOCOL
    assert result["coverage_fraction"] == 1
    np.testing.assert_allclose(result["signed_normal_khz_eq"], offset, atol=1e-8)
    if slope == 0:
        np.testing.assert_allclose(auto_t, manual_t)
        np.testing.assert_allclose(result["signed_normal_khz_eq"], auto_f[0]-manual_f[0])


@pytest.mark.parametrize("slope", [0.0, -2.0, -25.0])
def test_short_prediction_has_uncovered_normals(slope):
    manual_t = [20.0, 24.0]
    manual_f = [120.0, 120.0 + 4*slope]
    pred_t = np.array([20.8, 23.2])
    pred_f = np.interp(pred_t, manual_t, manual_f)
    result = compare_normal_curves(manual_t, manual_f, pred_t, pred_f,
                                   np.linspace(20, 24, 21))
    assert .5 < result["coverage_fraction"] < .7
    assert np.isnan(result["signed_normal_khz_eq"][0])
    assert np.isnan(result["signed_normal_khz_eq"][-1])


def test_knee_and_reference_from_manual_only():
    t = [20, 21, 22, 24]
    f = [100, 70, 75, 80]
    assert square_time_scale(t, f) == (7.5, "square")
    q = compare_normal_curves(t, f, t, f, np.linspace(20, 24, 41))
    assert q["coverage_fraction"] == 1
    assert np.max(np.abs(q["signed_normal_khz_eq"])) < 1e-8


def test_explicit_near_horizontal_fallback_and_invalid_inputs():
    assert square_time_scale([20, 24], [80, 80]) == (10, "near_horizontal_fallback")
    assert square_time_scale([20, 20.5], [150, 50]) == (200, "square")
    with pytest.raises(ValueError, match="invalid manual chirp"):
        square_time_scale([20, 20], [70, 80])
    with pytest.raises(ValueError, match="invalid predicted"):
        compare_normal_curves([20, 24], [80, 70], [22, 21], [75, 76], [22])
