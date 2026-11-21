"""Synthetic P2.1 feature and gate contracts, no corpus dependencies."""
import unittest

from experiments.p21_artifact_diagnostics import (
    RULES, curve_features, find_extra, gate_table,
)


def candidate():
    return dict(time_mid=0.002, peak_freq_hz=90000.0,
                width_ms=1.5, height_hz=22000.0,
                peak_db=14.0, slope_hz_per_ms=-5000.0)


def example(label, duration=1.6, sweep=18., tracked=True):
    features = {"track_duration_ms": duration,
                "track_frequency_drop_khz": sweep,
                "track_gap_ratio": 1.0} if tracked else None
    return {"label": label, "tracked": tracked, "features": features}


class P21Contracts(unittest.TestCase):
    def test_curve_features_use_detector_trajectory_only(self):
        points = [(0.001, 100000., 1.), (0.0015, 92000., 0.5),
                  (0.0025, 82000., 0.2)]
        features = curve_features(points, candidate())
        self.assertAlmostEqual(features["track_duration_ms"], 1.5)
        self.assertAlmostEqual(features["track_frequency_drop_khz"], 18.)
        self.assertEqual(features["track_points"], 3)
        self.assertAlmostEqual(features["candidate_blob_span_khz"], 22.)
        self.assertIsNone(curve_features(None, candidate()))

    def test_identity_requires_unique_candidate(self):
        case = {"candidate_peak_time_ms": 2.0,
                "candidate_peak_freq_khz": 90.0,
                "blob_width_ms": 1.5,
                "blob_frequency_span_khz": 22.0}
        index, found = find_extra(case, [candidate()])
        self.assertEqual(index, 0)
        self.assertEqual(found["peak_freq_hz"], 90000.0)
        with self.assertRaisesRegex(ValueError, "uniquely"):
            find_extra(case, [candidate(), candidate()])

    def test_gate_never_calls_untracked_negative_a_positive(self):
        rows = ([example("validated_chirp") for _ in range(22)] +
                [example("reviewed_artifact", duration=0.3, sweep=2.) for _ in range(7)] +
                [example("reviewed_artifact", tracked=False) for _ in range(6)])
        tab = gate_table(rows)
        self.assertEqual(tab["duration1_and_sweep8"]["rescued_chirps_retained_of_22"], 22)
        self.assertEqual(tab["duration1_and_sweep8"]["extra_negative_tracks_suppressed_of_7"], 7)
        self.assertFalse(tab["duration1_and_sweep8"]["independent_validation"])
        self.assertEqual(len(RULES), 5)

    def test_bad_cohort_counts_fail_closed(self):
        with self.assertRaisesRegex(ValueError, "unexpected membership"):
            gate_table([example("validated_chirp") for _ in range(22)])


if __name__ == "__main__":
    unittest.main()
