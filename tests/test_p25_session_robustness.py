"""P2.5 grouped robustness tests with hand-checked small populations."""
import unittest

from experiments.p25_session_robustness import (
    GATES, confidence_interval, empty_group, grouped_bootstrap,
    score_groups, select_grouping,
)


def group(manual, p0, p2, negatives, base_neg, add_neg, duration=3600.):
    r = empty_group()
    r["manual_refs"] = manual
    r["reference_weight_sum"] = float(manual)
    r["p0_weighted_mass"] = float(p0)
    r["p2_weighted_mass"] = float(p2)
    r["no_chirp_wavs"] = 1
    r["negative_duration_seconds"] = duration
    r["baseline_negative_tracks"] = base_neg
    for gate in GATES:
        r["gate_weighted_masses"][gate] = float(p2)
        r["gate_negative_tracks"][gate] = base_neg + add_neg
        r["gate_matched_refs"][gate] = manual
    return r


class P25ReportTests(unittest.TestCase):
    def test_grouped_paired_weighting_and_negative_rate(self):
        data = {
            "a": group(2, 1.0, 1.5, 1, 3, 1),
            "b": group(6, 2.0, 4.0, 1, 5, 2),
        }
        out = score_groups(data)
        self.assertEqual(out["manual_refs"], 8)
        self.assertEqual(out["reviewed_negative_wavs"], 2)
        self.assertAlmostEqual(out["p0_score"], 3/8)
        self.assertAlmostEqual(out["p2_score"], 5.5/8)
        self.assertEqual(out["gates"]["sweep_ge_8khz"]["negative_tracks"], 11)
        self.assertEqual(out["gates"]["sweep_ge_8khz"]["extra_negative_tracks_vs_v4"], 3)
        self.assertAlmostEqual(
            out["gates"]["sweep_ge_8khz"]["extra_negative_tracks_per_hour"], 1.5)

    def test_bootstrap_is_reproducible_and_no_independent_claim(self):
        rows = {"a": group(2, 1., 1.5, 1, 3, 1),
                "b": group(6, 2., 4., 1, 5, 2)}
        a = grouped_bootstrap(rows, iterations=100, seed=42)
        b = grouped_bootstrap(rows, iterations=100, seed=42)
        self.assertEqual(a, b)
        self.assertEqual(a["cluster_count"], 2)
        self.assertEqual(
            a["intervals"]["p2_all_extras"]["delta_vs_p0"]["n"], 100)

    def test_percentile_handles_missing_cohort(self):
        self.assertEqual(confidence_interval([None, None])["n"], 0)
        result = confidence_interval([1, 2, 3, 4])
        self.assertAlmostEqual(result["median"], 2.5)
        self.assertLess(result["p025"], result["p975"])

    def test_one_group_no_false_claim_of_generalization(self):
        r = group(3, 1., 2., 1, 0, 0)
        sample = grouped_bootstrap({"single": r}, iterations=10)
        self.assertEqual(sample["cluster_count"], 1)
        self.assertEqual(
            sample["intervals"]["sweep_ge_8khz"]["delta_vs_p2"]["n"], 10)

    def test_grouping_uses_bundle_if_manifest_session_not_informative(self):
        rows = {
            "calibration/wav/a": {"session": "all", "dataset_json": "a.json"},
            "calibration/wav/b": {"session": "all", "dataset_json": "b.json"},
        }
        mode, fn = select_grouping(rows)
        self.assertEqual(mode, "annotation_bundle")
        self.assertNotEqual(fn(rows["calibration/wav/a"]),
                            fn(rows["calibration/wav/b"]))


if __name__ == "__main__":
    unittest.main()
