"""Synthetic counterexamples for the geometry risks found in technical review.

The expected invariants deliberately fail while the reviewed defects remain.
Inputs are physical curves fixed independently of the production normalizer.
"""

import numpy as np
import pytest

from chirp_geometry import compare_normal_curves, square_time_scale
from laboratory.transverse_ridge import continuous_normal_ridge


NEAR_THRESHOLD_RATIOS = (0.9999, 1.0001)


@pytest.mark.parametrize("direction", [-1, 1], ids=["descending", "ascending"])
def test_time_scale_is_continuous_across_unit_slope(direction):
    scales = [
        square_time_scale([20, 24], [80, 80 + direction * 4 * ratio])[0]
        for ratio in NEAR_THRESHOLD_RATIOS
    ]
    # A 0.02% change in extent ratio must not change the time scale tenfold.
    assert abs(scales[1] - scales[0]) < 0.01, scales


@pytest.mark.parametrize("direction", [-1, 1], ids=["descending", "ascending"])
def test_fixed_vertical_offset_has_continuous_normal_error_at_unit_slope(direction):
    results = []
    for ratio in NEAR_THRESHOLD_RATIOS:
        slope = direction * ratio
        manual_t = np.array([20.0, 24.0])
        predicted_t = np.array([16.0, 28.0])
        # Same +4 kHz vertical displacement on both sides of the threshold;
        # never derive the synthetic prediction from square_time_scale.
        result = compare_normal_curves(
            manual_t, 80 + slope * (manual_t - 20),
            predicted_t, 84 + slope * (predicted_t - 20), [21, 22, 23],
        )
        assert result["coverage_fraction"] == 1.0
        assert np.all(result["signed_normal_khz_eq"] > 0)
        results.append(result["signed_normal_khz_eq"])

    np.testing.assert_allclose(results[0], results[1], rtol=0, atol=0.01)


@pytest.mark.parametrize("direction", [-1, 1], ids=["descending", "ascending"])
def test_transverse_ridge_offset_is_continuous_at_unit_slope(direction):
    times = np.linspace(16, 28, 481)
    freqs = np.linspace(64, 96, 641)
    offsets = []
    for ratio in NEAR_THRESHOLD_RATIOS:
        slope = direction * ratio
        # A deterministic Gaussian ridge, again displaced vertically by 4 kHz.
        centre = 84 + slope * (times - 20)
        db = 40 * np.exp(-0.5 * ((freqs[:, None] - centre) / 0.2) ** 2)
        result = continuous_normal_ridge(
            times, freqs, db, [20, 24], [80, 80 + 4 * slope],
            count=9, radius_khz=6, step_khz=0.025,
            anchor_penalty=0, continuity_penalty=0,
        )
        assert result["samples_evaluated"] == 9
        assert np.all(result["signed_normal_khz_eq"] > 0)
        offsets.append(result["signed_normal_khz_eq"])

    # Allow two offset-grid steps for quantization/interpolation, not a
    # change of approximately 1 kHz_eq induced solely by normalization.
    np.testing.assert_allclose(offsets[0], offsets[1], rtol=0, atol=0.05)


@pytest.mark.parametrize("direction", [-1, 1], ids=["descending", "ascending"])
def test_ordered_intersections_preserve_attainable_full_coverage(direction):
    manual_t = np.array([0.0, 4.0])
    manual_f = 80 + direction * manual_t
    predicted_t = np.array([0.0, 0.5, 1.0, 4.0])
    predicted_f = 80 + direction * np.array([2.0, 3.5, 1.0, -3.0])
    samples = np.array([1.0, 2.0])

    # Independent witness: the first two prediction vertices lie on the
    # respective normals, in increasing prediction order, inside radius 3.
    # The first normal also intersects (1, 80 + direction), at distance zero.
    # Taking that locally closer hit consumes the only hit of normal two.
    witness_t = predicted_t[:2]
    witness_f = predicted_f[:2]
    reference_f = 80 + direction * samples
    delta_t = witness_t - samples
    delta_f = witness_f - reference_f
    np.testing.assert_allclose(delta_t + direction * delta_f, 0, atol=1e-12)
    assert np.all(np.diff(witness_t) > 0)
    np.testing.assert_allclose(
        np.hypot(delta_t, delta_f), [np.sqrt(2), 1.5 * np.sqrt(2)],
    )
    assert np.all(np.hypot(delta_t, delta_f) < 3)

    result = compare_normal_curves(
        manual_t, manual_f, predicted_t, predicted_f, samples,
        max_radius_khz_eq=3,
    )
    assert result["coverage_fraction"] == 1.0, result["matched_times_ms"]
    assert result["covered"].tolist() == [True, True]
    assert np.all(np.diff(result["matched_times_ms"]) >= 0)
    assert np.all(result["abs_normal_khz_eq"] <= 3)
