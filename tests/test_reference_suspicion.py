"""Regression checks for the provisional reference-review score."""
import json

from laboratory.reference_suspicion import suspicion_metrics, SCORE_VERSION


def _clean(**updates):
    values = {
        "median_abs_normal_khz_eq": 0.3,
        "p90_abs_normal_khz_eq": 0.6,
        "median_signed_normal_khz_eq": 0.1,
        "support_fraction_3khz_eq": 1.0,
        "display_contrast_median_db": 18,
        "local_max_median_abs_khz_eq": 0.3,
        "local_peak_signed_median_khz_eq": 0.1,
        "error": "",
    }
    values.update(updates)
    return values


def test_good_reference_has_low_review_score():
    result = suspicion_metrics(_clean())
    assert result["suspicion_score"] == 0
    assert result["suspicion_reliability"] == "élevée"
    assert result["suspicion_score_version"] == SCORE_VERSION


def test_large_supported_offset_is_more_suspect():
    shifted = suspicion_metrics(_clean(
        median_abs_normal_khz_eq=5.5, p90_abs_normal_khz_eq=7,
        median_signed_normal_khz_eq=4.5,
        local_max_median_abs_khz_eq=5.5))
    assert shifted["suspicion_score"] >= 40
    assert "Décalage" in shifted["suspicion_reason"]
    assert sum(json.loads(shifted["suspicion_breakdown_json"]).values()) > 0


def test_weak_contrast_downweights_unreliable_ridge():
    bad = dict(median_abs_normal_khz_eq=5.5,
               p90_abs_normal_khz_eq=8,
               median_signed_normal_khz_eq=4.5,
               local_max_median_abs_khz_eq=5,
               local_peak_signed_median_khz_eq=4)
    clear = suspicion_metrics(_clean(**bad))
    weak = suspicion_metrics(_clean(**bad, display_contrast_median_db=2))
    assert weak["suspicion_reliability"] == "faible"
    assert weak["suspicion_score"] < clear["suspicion_score"]


def test_geometry_flag_is_not_lost_when_acoustics_fail():
    result = suspicion_metrics({"error": "Invalid points"}, "above_nyquist")
    assert result["suspicion_score"] == ""
    assert "Nyquist" in result["suspicion_reason"]
    assert result["suspicion_reliability"] == "indisponible"
    assert json.loads(result["suspicion_breakdown_json"])["geometrie"] == 20


def test_geometry_increases_ranking_and_is_capped():
    nominal = suspicion_metrics(_clean())
    bad = suspicion_metrics(_clean(), "nonincreasing_time;above_nyquist")
    assert bad["suspicion_score"] == nominal["suspicion_score"] + 20
    assert bad["suspicion_score"] <= 100
