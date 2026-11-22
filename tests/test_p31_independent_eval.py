"""P3.1 independent evaluator contracts with synthetic metadata ONLY.

These tests must run on calibration before any held-out access is authorized.
No WAVs, annotations, candidate audio or test manifests are loaded.
"""
import copy
import unittest

from experiments.p31_independent_eval import (
    DETECTOR_REFERENCE, MANIFEST_REFERENCE, PRIMARY,
    paired_statistics, quantile, safe_relative, validate_freeze,
)


SHA = "a" * 40
NONCE = "fixed-p31-one-shot"


def positive(group, number, baseline=False, primary=True, secondary=True):
    return {
        "file": f"calibration/wav/{group}.wav", "chirp_id": str(number),
        "group": group, "v4": baseline, "primary": primary, "secondary": secondary,
        "snr_db": 25.0,
        "v4_geometric_error_khz_eq": 2.0,
        "hybrid_geometric_error_khz_eq": 1.5,
    }


def negative(group, v4=1, primary=1, secondary=2):
    return {
        "file": f"calibration/wav/neg-{group}.wav", "group": group,
        "v4_tracks": v4, "primary_tracks": primary,
        "secondary_tracks": secondary, "negative_duration_seconds": 3600.0,
    }


def proof():
    return {
        "schema_version": 1, "status": "FROZEN",
        "p31_test_execution_approved": True,
        "scientific_auto_merge_authorized": False,
        "manifest_sha256": MANIFEST_REFERENCE, "evaluator_sha": SHA,
        "p30c_report_sha256": "a" * 64,
        "one_shot_request_nonce": NONCE,
    }


class P31SyntheticContract(unittest.TestCase):
    def test_freeze_required_and_exact_nonce(self):
        validate_freeze(proof(), SHA, NONCE)
        p = proof()
        p["status"] = "DRAFT"
        with self.assertRaisesRegex(ValueError, "freeze certificate"):
            validate_freeze(p, SHA, NONCE)
        with self.assertRaisesRegex(ValueError, "freeze certificate"):
            validate_freeze(proof(), SHA, "other")

    def test_heldout_paths_not_allowed_as_calibration(self):
        with self.assertRaisesRegex(ValueError, "partition-relative"):
            safe_relative("test/wav/secret.wav", "calibration/wav/")
        with self.assertRaisesRegex(ValueError, "partition-relative"):
            safe_relative("test/wav/../annotations/secret.json", "test/wav/")
        self.assertEqual(safe_relative("test/wav/a.wav", "test/wav/"),
                         "test/wav/a.wav")

    def test_full_session_clustered_superiority_requires_no_negative_increase(self):
        pos = [positive(g, i) for g in ["one", "two", "three"] for i in range(10)]
        neg = [negative(g, 1, 1, 2) for g in ["one", "two", "three"]]
        stats = paired_statistics(pos, neg, replicates=100, seed=42)
        self.assertEqual(stats["recording_groups"], 3)
        self.assertEqual(stats["primary_paired_recall_delta"], 1.0)
        self.assertTrue(stats["negative_nonregression"])
        self.assertTrue(stats["promotion_candidate_eligible_for_human_review"])
        self.assertFalse(stats["scientific_auto_merge_authorized"])

    def test_negative_increase_vetoes_even_with_perfect_recall(self):
        pos = [positive(g, i) for g in ["one", "two", "three"] for i in range(10)]
        neg = [negative(g, 1, 2, 2) for g in ["one", "two", "three"]]
        stats = paired_statistics(pos, neg, replicates=100)
        self.assertFalse(stats["negative_nonregression"])
        self.assertFalse(stats["promotion_candidate_eligible_for_human_review"])

    def test_single_session_never_promotes(self):
        pos = [positive("one", i) for i in range(10)]
        neg = [negative("one")]
        stats = paired_statistics(pos, neg, replicates=100)
        self.assertIsNone(stats["primary_recall_cluster_ci95"])
        self.assertFalse(stats["promotion_candidate_eligible_for_human_review"])
        self.assertTrue(any("Fewer than 3" in x for x in stats["limitations"]))

    def test_unreviewed_positives_are_not_negative_surrogates(self):
        pos = [positive(g, i) for g in ["one", "two", "three"] for i in range(10)]
        stats = paired_statistics(pos, [], replicates=100)
        self.assertEqual(stats["reviewed_negative_wavs"], 0)
        self.assertFalse(stats["negative_nonregression"])
        self.assertFalse(stats["promotion_candidate_eligible_for_human_review"])

    def test_duplicate_reference_is_fatal(self):
        pos = [positive("one", "x"), positive("one", "x")]
        with self.assertRaisesRegex(ValueError, "counted twice"):
            paired_statistics(pos, [], replicates=100)

    def test_no_automatic_tuning_of_secondary(self):
        pos = [positive("one", "x", baseline=True, primary=False, secondary=True)]
        stats = paired_statistics(pos, [negative("one")], replicates=100)
        self.assertEqual(stats["primary_paired_recall_delta"], -1.0)
        self.assertFalse(stats["promotion_candidate_eligible_for_human_review"])

    def test_secondary_geometry_and_snr_are_descriptive_only(self):
        pos = [positive(g, i, baseline=True, primary=True)
               for g in ["one", "two", "three"] for i in range(3)]
        neg = [negative(g) for g in ["one", "two", "three"]]
        stats = paired_statistics(pos, neg, replicates=100)
        self.assertEqual(stats["snr_strata"]["medium_15_35"]["manual_refs"], 9)
        self.assertEqual(
            stats["paired_geometric_diagnostics"][
                "mean_primary_minus_v4_absolute_perpendicular_error_khz_eq"], -.5)
        self.assertFalse(
            stats["paired_geometric_diagnostics"]["signed_perpendicular_error_available"])
        self.assertFalse(stats["promotion_candidate_eligible_for_human_review"])

    def test_quantiles_deterministic(self):
        self.assertEqual(quantile([1, 3, 5], .5), 3.0)
        self.assertEqual(quantile([1, 3, 5], 0), 1.0)


if __name__ == "__main__":
    unittest.main()
