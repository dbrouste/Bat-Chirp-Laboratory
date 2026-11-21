"""P2.4 synthetic complete-cohort scoring contracts, no WAV dependency."""
import unittest

from experiments.p24_full_calibration_gates import (
    GATES, boolean, evaluate_gate_records, passes,
)


def extra(sweep=15., tracked=True, contrast=10.):
    return {
        "tracked": tracked,
        "track_sweep_khz": sweep if tracked else None,
        "normal": ({
            "normal_contrast_4khz_median_db": contrast,
            "normal_contrast_8khz_median_db": contrast,
            "normal_center_dominant_fraction": .9,
            "normal_pm2_energy_fraction_median": .75,
        } if tracked and sweep >= 8. else None),
    }


def corpus():
    positive = []
    for j in range(493):
        row = {
            "file": "calibration/wav/example.wav",
            "chirp_id": f"chirp-{j:04d}",
            "snr_db": "40.0",
            "hybrid_matched": "True" if j < 423 else "False",
            "hybrid_tracked": "True" if j < 423 else "False",
            "hybrid_tolerance_fraction": "1.0" if j < 423 else "",
        }
        if 401 <= j < 423:
            row["_extra"] = extra(15., contrast=8. if j < 421 else 2.)
        positive.append(row)
    negative = [
        {"baseline_tracked_proposals": "524" if j == 0 else "0",
         "added_tracked_proposals": "7" if j == 0 else "0",
         "_extras": ([extra(2.) for _ in range(3)] +
                     [extra(15., contrast=-3.) for _ in range(4)] +
                     [extra(tracked=False) for _ in range(6)])
                    if j == 0 else []}
        for j in range(203)
    ]
    p2 = {
        "hybrid": {"weighted_full_pipeline_within_tolerance_fraction": 423/493},
        "baseline": {"weighted_full_pipeline_within_tolerance_fraction": 401/493},
    }
    return positive, negative, p2


class P24Tests(unittest.TestCase):
    def test_full_cohort_retains_old_seeds_and_reconciles_negative_delta(self):
        pos, neg, p2 = corpus()
        output = evaluate_gate_records(pos, neg, p2)
        self.assertEqual(output["p2_all_extras"]["negative_tracked_proposals"], 531)
        self.assertEqual(output["p2_all_extras"]["matched_manual_refs_after_gate_conservative"], 423)
        self.assertEqual(output["sweep_ge_8khz"]["negative_tracked_proposals"], 528)
        self.assertEqual(output["sweep_ge_8khz"]["matched_manual_refs_after_gate_conservative"], 423)
        self.assertEqual(output["sweep8_and_normal_contrast4_ge4"]["matched_manual_refs_after_gate_conservative"], 421)
        self.assertEqual(output["sweep8_and_normal_contrast4_ge4"]["negative_tracked_proposals"], 524)
        self.assertEqual(len(output), len(GATES))
        self.assertTrue(all(
            v["independent_validation"] is False and
            v["scientific_merge_authorized"] is False
            for v in output.values()))

    def test_old_v4_matched_reference_not_rejected(self):
        pos, neg, p2 = corpus()
        for row in pos[:401]:
            self.assertNotIn("_extra", row)
        output = evaluate_gate_records(pos, neg, p2)
        self.assertGreaterEqual(
            output["sweep8_and_normal_contrast4_ge4"][
                "matched_manual_refs_after_gate_conservative"], 401)

    def test_missing_normal_fails_closed(self):
        with self.assertRaisesRegex(ValueError, "missing fixed-STFT"):
            passes("sweep8_and_normal_contrast4_ge4",
                   {"tracked": True, "track_sweep_khz": 15., "normal": None})

    def test_fingerprint_of_boolean_fields_cannot_silently_change(self):
        with self.assertRaises(ValueError):
            boolean("yes")
        with self.assertRaises(ValueError):
            boolean("")
        self.assertTrue(boolean("True"))
        self.assertFalse(boolean("False"))

    def test_short_reference_cohort_is_rejected(self):
        pos, neg, p2 = corpus()
        with self.assertRaisesRegex(ValueError, "full calibration"):
            evaluate_gate_records(pos[:100], neg, p2)


if __name__ == "__main__":
    unittest.main()
