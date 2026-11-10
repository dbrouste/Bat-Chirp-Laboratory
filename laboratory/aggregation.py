"""Independent aggregation of the existing SNR benchmark's per-WAV results.

This evaluator stays in the laboratory, not inside a codebase that the future
optimization agent will be allowed to modify. Geometry v1 is separate.
"""

from __future__ import annotations

from statistics import fmean


def _mean(values):
    values = list(values)
    return float(fmean(values)) if values else None


def summarize_importance(details):
    if not details:
        return {"weighted_detector_recall": None,
                "weighted_full_pipeline_within_tolerance_fraction": None,
                "weighted_missed_chirp_mass": 0.0,
                "total_chirp_importance": 0.0}
    weights = [float(row["detection_importance_weight"]) for row in details]
    total = sum(weights)
    if total <= 0:
        raise ValueError("Invalid total detection weight")
    return {
        "weighted_detector_recall": sum(
            w for w, row in zip(weights, details) if row.get("detector_matched")) / total,
        "weighted_full_pipeline_within_tolerance_fraction": sum(
            w * row.get("within_snr_tolerance_fraction", 0.0)
            for w, row in zip(weights, details)) / total,
        "weighted_missed_chirp_mass": sum(
            w for w, row in zip(weights, details) if not row.get("detector_matched")),
        "total_chirp_importance": float(total),
    }


def summarize_by_snr(details):
    groups = (
        ("low_15_db_or_less", lambda snr: snr <= 15),
        ("medium_15_to_35_db", lambda snr: 15 < snr < 35),
        ("high_35_db_or_more", lambda snr: snr >= 35),
    )
    result = {}
    for label, predicate in groups:
        rows = [r for r in details if r.get("snr_max_db") is not None
                and predicate(r["snr_max_db"])]
        tracked = [r for r in rows if r.get("tracking_success")
                   and r.get("mean_full_interval_error_khz") is not None]
        n = len(rows)
        matched = sum(bool(r.get("detector_matched")) for r in rows)
        result[label] = {
            "annotated": n, "detector_matched": matched,
            "tracking_succeeded": sum(bool(r.get("tracking_success")) for r in rows),
            "mean_tracking_error_khz": _mean(r["mean_full_interval_error_khz"]
                                             for r in tracked),
            "mean_normalized_error": _mean(r["mean_normalized_error"] for r in tracked),
            "mean_coverage_fraction": _mean(r["coverage_fraction"] for r in tracked),
            "mean_start_error_ms": _mean(r["start_error_ms"] for r in tracked),
            "mean_end_error_ms": _mean(r["end_error_ms"] for r in tracked),
            "detector_recall": matched / n if n else None,
            "full_pipeline_within_tolerance_fraction": _mean(
                r.get("within_snr_tolerance_fraction", 0.0) for r in rows),
        }
    return result


def summarize_results(per_file, details, *, low_snr_importance_weight=0.2):
    totals = {
        "files": len(per_file),
        "files_evaluated": sum("error" not in r for r in per_file),
        "files_missing": sum("error" in r for r in per_file),
        "annotated": sum(r.get("annotated", 0) for r in per_file),
        "candidates": sum(r.get("candidates", 0) for r in per_file),
        "matched": sum(r.get("matched", 0) for r in per_file),
        "tracked": sum(r.get("tracking_succeeded", 0) for r in per_file),
        "mean_tracking_error_khz": _mean(r["mean_full_interval_error_khz"]
                                         for r in details
                                         if r.get("mean_full_interval_error_khz") is not None),
        "mean_normalized_error": _mean(r["mean_normalized_error"]
                                      for r in details if r.get("mean_normalized_error") is not None),
        "mean_within_snr_tolerance_fraction": _mean(r["within_snr_tolerance_fraction"]
                                                   for r in details if r.get("within_snr_tolerance_fraction") is not None),
        "full_pipeline_within_tolerance_fraction": _mean(
            r.get("within_snr_tolerance_fraction", 0.0) for r in details),
        "by_snr": summarize_by_snr(details),
        "ignored_automatic_proposals": sum(
            r.get("ignored_automatic_proposals", 0) for r in per_file),
        "snr_tolerance_khz": {"at_or_below_15_db": 5.0,
                              "at_or_above_35_db": 3.0},
        "detection_importance": {"at_or_below_15_db": low_snr_importance_weight,
                                 "at_or_above_35_db": 1.0},
    }
    totals.update(summarize_importance(details))
    return totals
