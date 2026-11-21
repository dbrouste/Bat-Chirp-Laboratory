"""P2.2 fixed-STFT evidence and diagnostic gates: synthetic regression tests."""
import unittest

import numpy as np
from benchmark.snr_metrics import ReferenceSNR
from experiments.p22_spectral_echo_diagnostics import (
    DIAGNOSTIC_GATES, score_predeclared_gates, spectral_features,
)


class SpectralContracts(unittest.TestCase):
    @staticmethod
    def echo_test():
        freq = np.arange(0, 150001, 1000, dtype=float)
        times = np.linspace(0.0, 0.014, 141)
        image = np.zeros((len(freq), len(times)), dtype=np.float32)
        for j, t in enumerate(times):
            if 0.002 <= t <= 0.008:
                f = 100000.0 - (t - 0.002) * 60000.0 / 0.006
                i = int(round(f / 1000.0))
                image[max(0, i-1):i+2, j] = 22.0
        curve = np.column_stack((np.linspace(0.0025, 0.0075, 45),
                                 np.linspace(95000., 45000., 45)))
        return curve, ReferenceSNR(freq, times, image)

    def test_sloped_ridge_detects_support_without_annotation(self):
        curve, ref = self.echo_test()
        f = spectral_features(curve, ref)
        self.assertGreater(f["valid_spectral_samples"], 50)
        self.assertGreater(f["snr_median_db"], 12.0)
        self.assertGreater(f["snr_support_10db_fraction"], 0.7)
        self.assertGreater(f["flank_contrast_median_db"], 8.0)
        self.assertLess(f["stationary_occupancy_10db_fraction"], 0.20)
        self.assertIsNotNone(f["delayed_same_frequency_support_max_fraction"])

    def test_stationary_interference_is_proxy_not_ground_truth(self):
        freq = np.arange(0, 150001, 1000, dtype=float)
        times = np.linspace(0.0, 0.014, 141)
        image = np.zeros((len(freq), len(times)), dtype=np.float32)
        image[60, :] = 20.0
        ref = ReferenceSNR(freq, times, image)
        curve = np.column_stack((np.linspace(0.004, 0.007, 40),
                                 np.full(40, 60000.0)))
        f = spectral_features(curve, ref)
        self.assertGreater(f["stationary_occupancy_10db_fraction"], 0.95)
        self.assertGreater(f["delayed_same_frequency_support_max_fraction"], 0.95)
        self.assertEqual(f["snr_support_10db_fraction"], 1.0)

    def test_all_predeclared_gates_are_exploratory(self):
        good = {
            "snr_median_db": 12., "snr_support_6db_fraction": 0.9,
            "snr_support_10db_fraction": 0.8, "flank_contrast_median_db": 12.,
            "stationary_occupancy_10db_fraction": 0.02,
            "delayed_same_frequency_support_max_fraction": 0.1,
        }
        bad = {
            **good, "snr_median_db": 1.0,
            "snr_support_6db_fraction": 0.15,
            "snr_support_10db_fraction": 0.1,
            "flank_contrast_median_db": -4.,
            "stationary_occupancy_10db_fraction": 0.75,
            "delayed_same_frequency_support_max_fraction": 0.90,
        }
        rows = ([{"cohort": "rescued_positive", **good} for _ in range(22)] +
                [{"cohort": "remaining_tracked_artifact", **bad} for _ in range(4)])
        report = score_predeclared_gates(rows)
        self.assertEqual(len(report), len(DIAGNOSTIC_GATES))
        self.assertTrue(all(d["rescued_chirps_retained_of_22"] == 22 and
                            d["remaining_artifact_tracks_rejected_of_4"] == 4 and
                            d["independent_validation"] is False
                            for d in report.values()))

    def test_fail_closed_if_controls_missing(self):
        with self.assertRaisesRegex(ValueError, "not 22 vs 4"):
            score_predeclared_gates(
                [{"cohort": "remaining_tracked_artifact"} for _ in range(4)])


if __name__ == "__main__":
    unittest.main()
