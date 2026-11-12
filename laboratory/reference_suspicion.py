"""Explainable, provisional 0–100 ranking of manually annotated reference chirps.

This is a *review priority*, never an error probability or a reason to reject
an annotation automatically. All thresholds must be calibrated on the
calibration split, independently of the held-out test data.
"""
from __future__ import annotations

import json
import math

SCORE_VERSION = "reference_suspicion_v1_uncalibrated"
GEOMETRY_WEIGHTS = {
    "invalid_points": 20,
    "nonfinite_points": 20,
    "nonincreasing_time": 18,
    "zero_duration": 20,
    "outside_wav_duration": 20,
    "above_nyquist": 20,
    "nonpositive_frequency": 18,
    "multiple_direction_changes": 9,
    "large_frequency_step": 8,
    "steep_local_segment": 7,
    "unusual_duration": 5,
    "frequency_outside_review_band": 4,
    "many_points": 2,
}
GEOMETRY_REASONS = {
    "invalid_points": "Points invalides",
    "nonfinite_points": "Coordonnées non finies",
    "nonincreasing_time": "Temps non croissants",
    "zero_duration": "Durée nulle",
    "outside_wav_duration": "Annotation hors WAV",
    "above_nyquist": "Fréquence au-dessus de Nyquist",
    "nonpositive_frequency": "Fréquence non positive",
    "multiple_direction_changes": "Retournements multiples",
    "large_frequency_step": "Saut fréquentiel",
    "steep_local_segment": "Pente locale abrupte",
    "unusual_duration": "Durée inhabituelle",
    "frequency_outside_review_band": "Fréquence inhabituelle",
    "many_points": "Nombre de points élevé",
}


def _numeric(row: dict, name: str) -> float:
    value = float(row[name])
    if not math.isfinite(value):
        raise ValueError(f"Non-finite audit metric: {name}")
    return value


def _ramp(value: float, low: float, high: float, points: float) -> float:
    return points * max(0.0, min(1.0, (value - low) / (high - low)))


def suspicion_metrics(acoustic: dict, geometry_flags: str = "") -> dict:
    """Combine independent geometric checks and acoustic ridge diagnostics.

    The normal distances are *kHz_eq* in the annotation-derived square frame.
    Weak contrasts downweight ridge-distance evidence: uncertainty alone is
    not evidence that a human reference is incorrect.
    """
    flags = [flag for flag in str(geometry_flags or "").split(";") if flag]
    geometry = min(20.0, sum(GEOMETRY_WEIGHTS.get(flag, 0) for flag in set(flags)))
    blank = {
        "suspicion_score": "",
        "suspicion_reason": "; ".join(GEOMETRY_REASONS.get(f, f) for f in flags)
                            or "Analyse acoustique indisponible",
        "suspicion_reliability": "indisponible",
        "suspicion_breakdown_json": json.dumps({
            "acoustique": None, "local": None,
            "geometrie": round(geometry, 2), "ambiguite": None,
        }, ensure_ascii=False),
        "suspicion_score_version": SCORE_VERSION,
        "quality_flags": ";".join(flags),
    }
    if acoustic.get("error"):
        return blank
    try:
        median = _numeric(acoustic, "median_abs_normal_khz_eq")
        p90 = _numeric(acoustic, "p90_abs_normal_khz_eq")
        signed = abs(_numeric(acoustic, "median_signed_normal_khz_eq"))
        support = _numeric(acoustic, "support_fraction_3khz_eq")
        contrast = _numeric(acoustic, "display_contrast_median_db")
        local_max = _numeric(acoustic, "local_max_median_abs_khz_eq")
        local_peak = abs(_numeric(acoustic, "local_peak_signed_median_khz_eq"))
    except (KeyError, ValueError, TypeError, OverflowError):
        return blank

    # Weak signal: do not treat an unreliable alternative ridge as truth.
    reliability = ("élevée" if contrast >= 10 else
                   "moyenne" if contrast >= 6 else "faible")
    factor = 1.0 if contrast >= 10 else (0.7 if contrast >= 6 else 0.35)
    acoustic_points = factor * (
        _ramp(median, 0.8, 5.0, 20)
        + _ramp(p90, 1.5, 8.0, 10)
        + _ramp(signed, 0.8, 4.0, 5)
        + _ramp(0.85 - support, 0.0, 0.85, 5)
    )
    local_points = factor * (
        _ramp(local_max, 0.8, 5.0, 17)
        + _ramp(local_peak, 0.8, 4.5, 8)
    )
    ambiguity = _ramp(12 - contrast, 0, 12, 15)
    components = {
        "acoustique": round(min(40, acoustic_points), 2),
        "local": round(min(25, local_points), 2),
        "geometrie": round(geometry, 2),
        "ambiguite": round(min(15, ambiguity), 2),
    }
    score = int(round(sum(components.values())))
    explanations = []
    if median > 3 or signed > 2:
        explanations.append("Décalage par rapport à la crête")
    elif median > 1.5:
        explanations.append("Écart spectral modéré")
    if p90 > 5 or local_max > 2.5 or local_peak > 2:
        explanations.append("Déviation locale ou extrémité")
    elif local_max > 1.5:
        explanations.append("Écart local modéré")
    if support < 0.5:
        explanations.append("Soutien spectral discontinu")
    if flags:
        explanations.extend(GEOMETRY_REASONS.get(f, f) for f in flags[:2])
    if contrast < 6:
        explanations.append("Signal acoustique ambigu")
    elif contrast < 10:
        explanations.append("Contraste spectral modéré")
    if not explanations:
        explanations.append("Aucune anomalie marquée")
    return {
        "suspicion_score": score,
        "suspicion_reason": "; ".join(dict.fromkeys(explanations)),
        "suspicion_reliability": reliability,
        "suspicion_breakdown_json": json.dumps(components, ensure_ascii=False),
        "suspicion_score_version": SCORE_VERSION,
        "quality_flags": ";".join(flags),
    }
