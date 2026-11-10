"""Score real detector trajectories against manual chirps, independently.

The algorithm is imported from an external checkout. Human annotations are
used only *after* the detector and tracking have produced their proposals.
Every proposal is tracked, including overlapping calls and harmonics.
"""

from __future__ import annotations

from time import perf_counter

import numpy as np
from scipy.ndimage import map_coordinates
from scipy.interpolate import PchipInterpolator
from scipy.signal import stft

from .geometry import GeometryConfig, _curve, match_curves


def reference_snr_max(audio, sample_rate: int, manual: list) -> list[float]:
    """Fixed Flattop 512/hop64 STFT, SNR sampled along manual curves only."""
    if not manual:
        return []
    freqs, times, spectrum = stft(
        audio, fs=sample_rate, window="flattop", nperseg=512,
        noverlap=448, boundary=None, padded=False,
    )
    if len(times) < 2:
        raise ValueError("WAV too short for reference SNR STFT")
    power = 20 * np.log10(np.maximum(abs(spectrum), 1e-15))
    snr = power - np.median(power, axis=1)[:, None]
    output = []
    for points in manual:
        curve = _curve(points)
        t = np.linspace(curve[0, 0], curve[-1, 0], 50) / 1000
        f = ((np.interp(t * 1000, curve[:, 0], curve[:, 1]) if len(curve) == 2
              else PchipInterpolator(curve[:, 0], curve[:, 1])(t * 1000))
             * 1000)
        if t[0] < 0 or t[-1] > len(audio) / sample_rate:
            raise ValueError("Manual annotation outside WAV duration")
        if np.any(f > freqs[-1]) or np.any(f < 0):
            raise ValueError("Manual annotation outside WAV Nyquist range")
        fi = f / (freqs[1] - freqs[0])
        ti = (t - times[0]) / (times[1] - times[0])
        spread = [map_coordinates(snr, np.stack((fi + shift, ti)),
                                  order=1, mode="nearest")
                  for shift in (-1, 0, 1)]
        output.append(float(np.max(np.maximum.reduce(spread))))
    return output


def evaluate_file(name: str, record: dict, wav_root: str, options: dict,
                  geometry_options: dict) -> dict:
    """Return a scored single-WAV result for the laboratory runner."""
    import soundfile as sf
    from bat_analysis.detection import detect_candidates_snr_blobs
    from bat_analysis.reassignment_tracking import track_reassigned_candidate
    from pathlib import Path

    started = perf_counter()
    wav = Path(wav_root) / name
    y, sample_rate = sf.read(wav, always_2d=False)
    if y.ndim == 2:
        y = np.mean(y, axis=1)
    human = [chirp for chirp in record.get("chirps", [])
             if isinstance(chirp, dict) and chirp.get("source") == "manual"
             and chirp.get("status") == "validated"]
    if options.get("max_per_file") is not None:
        human = human[:options["max_per_file"]]
    manual_points = [chirp["points"] for chirp in human]
    snrs = reference_snr_max(y, int(sample_rate), manual_points)
    references = [dict(id=chirp.get("chirp_id", i), points=chirp["points"],
                       snr_max_db=snr)
                  for i, (chirp, snr) in enumerate(zip(human, snrs))]

    detector_kwargs = {}
    if options.get("v4_general_min_width_ms") is not None:
        detector_kwargs["v4_general_min_width_ms"] = options[
            "v4_general_min_width_ms"]
    candidates = detect_candidates_snr_blobs(
        np.asarray(y), int(sample_rate), slope_filter_mode="adaptive_v4",
        **detector_kwargs)
    predictions = []
    failures = []
    for i, candidate in enumerate(candidates):
        tracking_kwargs = {}
        if options.get("search_margin_ms") is not None:
            tracking_kwargs["search_margin_ms"] = options["search_margin_ms"]
        diagnostic = {}
        try:
            curve = track_reassigned_candidate(
                y, int(sample_rate), candidate,
                n_fft=options.get("n_fft"), hop=options.get("hop"),
                use_broad_seed=True, complete_ends=True,
                trim_echo_rebound=options.get("trim_echo_rebound", True),
                diagnostics=diagnostic, **tracking_kwargs)
            if curve is not None:
                arr = np.asarray(curve, dtype=float)
                if arr.ndim != 2 or len(arr) < 2 or arr.shape[1] < 2:
                    raise ValueError("Tracker returned an invalid trajectory")
                predictions.append({"id": i, "points": (
                    arr[:, :2] * [1000, 0.001]).tolist()})
        except Exception as exc:
            failures.append({"candidate_index": i,
                             "error": f"{type(exc).__name__}: {exc}"})

    config = GeometryConfig(**geometry_options)
    result = match_curves(references, predictions, config,
                          exhaustive=(record.get("annotation_exhaustive") is True
                                      and options.get("max_per_file") is None))
    details = [{"file": name, "chirp_id": row["manual_id"], **row}
               for row in result["details"]]
    per_file = {
        "file": name, "annotated": len(references),
        "candidates": len(candidates), "tracked": len(predictions),
        "tracking_errors": failures,
        "matched": sum(row["detected"] for row in details),
        "weighted_recall": result["weighted_recall"],
        "weighted_trajectory_score": result["weighted_trajectory_score"],
        "false_positive_count": result["false_positive_count"],
        "elapsed_s": perf_counter() - started,
    }
    return {"per_file": [per_file], "details": details,
            "unmatched_automatic": result["unmatched_automatic"]}


def aggregate(per_file: list[dict], details: list[dict]) -> dict:
    weights = [row["snr_weight"] for row in details]
    total = sum(weights)
    return {
        "files": len(per_file),
        "files_evaluated": sum("error" not in row for row in per_file),
        "files_missing": sum("error" in row for row in per_file),
        "annotated": len(details),
        "candidates": sum(row.get("candidates", 0) for row in per_file),
        "tracked": sum(row.get("tracked", 0) for row in per_file),
        "matched": sum(bool(row["detected"]) for row in details),
        "weighted_recall": (sum(row["snr_weight"] for row in details
                                if row["detected"]) / total if total else None),
        "weighted_trajectory_score": (
            sum(row["snr_weight"] * row["score"] for row in details)
            / (100 * total) if total else None),
        "false_positive_count": (sum(row["false_positive_count"] for row in per_file)
                                 if per_file and all(row.get("false_positive_count")
                                                     is not None for row in per_file)
                                 else None),
    }
