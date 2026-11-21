"""Synthetic P2.3 diagnostics, independent of the annotated corpus."""
import unittest

import numpy as np
from benchmark.snr_metrics import ReferenceSNR
from experiments.p23_perpendicular_ridge import (
    GATES, normal_profiles, sample_bilinear, score_fixed_gates,
)


def fixture(*, broad=False):
    frequencies = np.arange(0., 160001., 1000.)
    times = np.linspace(0., .014, 141)
    predicted = np.column_stack((
        np.linspace(.003, .007, 45),
        np.linspace(100000., 48000., 45),
    ))
    if broad:
        power = np.full((len(frequencies), len(times)), 18., dtype=np.float32)
    else:
        centers = 100000. - (times - .003) * 52000. / .004
        power = (25.*np.exp(
            -0.5*((frequencies[:, None]-centers[None, :])/1500.)**2)
                 ).astype(np.float32)
    return predicted, ReferenceSNR(frequencies, times, power)


class NormalGeometryTests(unittest.TestCase):
    def test_perpendicular_narrow_ridge_is_concentrated(self):
        curve, stft = fixture()
        d = normal_profiles(curve, stft)
        self.assertGreater(d["normal_valid_profile_count"], 40)
        self.assertGreater(d["normal_contrast_4khz_median_db"], 4.)
        self.assertGreater(d["normal_center_dominant_fraction"], .8)
        self.assertLess(d["normal_6db_width_median_khz_eq"], 10.)
        self.assertGreater(d["normal_pm2_energy_fraction_median"], .4)

    def test_broad_energy_not_expressed_as_narrow_ridge(self):
        curve, stft = fixture(broad=True)
        d = normal_profiles(curve, stft)
        self.assertAlmostEqual(d["normal_contrast_4khz_median_db"], 0., places=4)
        self.assertAlmostEqual(d["normal_pm2_energy_fraction_median"], 3/9, places=4)
        self.assertGreater(d["normal_6db_width_median_khz_eq"], 10.)

    def test_sampling_outside_fixed_stft_returns_nan(self):
        _, stft = fixture()
        sampled = sample_bilinear(
            stft, np.asarray([.005, -.001]), np.asarray([70000., 70000.]))
        self.assertTrue(np.isfinite(sampled[0]))
        self.assertTrue(np.isnan(sampled[1]))

    def test_no_exploratory_gate_claims_independent_validation(self):
        curve, stft = fixture()
        good = normal_profiles(curve, stft)
        bad = normal_profiles(*fixture(broad=True))
        rows = ([{"cohort": "rescued_positive", **good} for _ in range(22)]
                + [{"cohort": "remaining_tracked_artifact", **bad}
                   for _ in range(4)])
        results = score_fixed_gates(rows)
        self.assertEqual(len(results), len(GATES))
        self.assertTrue(all(r["independent_validation"] is False
                            for r in results.values()))
        self.assertEqual(
            results["normal_contrast_4khz_ge_4db"]["chirps_retained_of_22"], 22)
        self.assertEqual(
            results["normal_contrast_4khz_ge_4db"]["artifact_tracks_rejected_of_4"], 4)

    def test_missing_cases_fail_closed(self):
        with self.assertRaisesRegex(ValueError, "cohort mismatch"):
            score_fixed_gates([{"cohort": "remaining_tracked_artifact"}]*4)


if __name__ == "__main__":
    unittest.main()
