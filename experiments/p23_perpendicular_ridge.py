"""P2.3: spectral ridge concentration on predicted-trajectory normals.

Calibration-only exploratory comparison, *not* classifier training. The 26
cases are the same P1/P2.1/P2.2 reviewed development examples (22 rescued
chirps, 4 surviving artifacts). All profile coordinates are determined solely
by predicted tracks; manual references provide class labels and pairing only.
No held-out test WAVs/JSON, no original files written, no scientific promotion.

Independent fixed Flattop STFT 512/hop64 per P0, bilinear sampling without
peak-max interpolation to avoid artificially broadening narrow ridge profiles.
The predicted curve provides its own time-frequency scale, never a manual one.
"""
from __future__ import annotations

import argparse
import csv
import json
import math
import statistics
from collections import defaultdict
from pathlib import Path

from experiments.p21_artifact_diagnostics import (
    DETECTOR_REFERENCE, LAB_REFERENCE, MANIFEST_REFERENCE,
    P2_SHA, checked_path, csv_rows, curve_features, find_extra,
    hybrid_candidates, require, revision, sha256, track, validate_p2,
)

OFFSETS = (-8., -6., -4., -2., 0., 2., 4., 6., 8.)
# Fixed diagnostic hypotheses: NOT tuned to the 26 examples.
GATES = {
    "normal_contrast_4khz_ge_4db":
        lambda x: x["normal_contrast_4khz_median_db"] >= 4.,
    "normal_contrast_8khz_ge_6db":
        lambda x: x["normal_contrast_8khz_median_db"] >= 6.,
    "normal_center_dominant_ge_60pct":
        lambda x: x["normal_center_dominant_fraction"] >= .60,
    "normal_6db_width_le_6khz_eq":
        lambda x: x["normal_6db_width_median_khz_eq"] <= 6.,
    "normal_energy_pm2_fraction_ge_45pct":
        lambda x: x["normal_pm2_energy_fraction_median"] >= .45,
    "normal_contrast4_and_center_dominant":
        lambda x: (x["normal_contrast_4khz_median_db"] >= 4.
                   and x["normal_center_dominant_fraction"] >= .60),
}
FEATURES = (
    "track_sweep_khz", "normal_time_scale_khz_per_ms",
    "normal_valid_profile_count", "normal_center_snr_median_db",
    "normal_contrast_4khz_median_db", "normal_contrast_8khz_median_db",
    "normal_center_dominant_fraction", "normal_peak_offset_abs_median_khz_eq",
    "normal_6db_width_median_khz_eq",
    "normal_pm2_energy_fraction_median",
)


def sample_bilinear(ref, times_s, freqs_hz):
    """Return nan outside the fixed STFT support (never use extrapolated data)."""
    import numpy as np
    from scipy.ndimage import map_coordinates

    t = np.asarray(times_s, dtype=float)
    f = np.asarray(freqs_hz, dtype=float)
    require(t.shape == f.shape and np.isfinite(t).all()
            and np.isfinite(f).all(), "invalid per-normal spectrogram coordinates")
    valid = ((t >= float(ref.times_s[0])) & (t <= float(ref.times_s[-1]))
             & (f >= float(ref.freqs_hz[0]))
             & (f <= float(ref.freqs_hz[-1])))
    result = np.full(t.shape, np.nan, dtype=float)
    if np.any(valid):
        fi = np.interp(f[valid], ref.freqs_hz,
                       np.arange(len(ref.freqs_hz)))
        ti = np.interp(t[valid], ref.times_s,
                       np.arange(len(ref.times_s)))
        result[valid] = map_coordinates(
            ref.snr_db, np.stack((fi, ti)), order=1, mode="nearest")
    return result


def normal_profiles(curve, ref):
    """Return robust transverse ridge measurements in a predicted-only metric.

    In the square plane (u = scale * t_ms, v = f_khz), the local unit normal
    to tangent (scale, slope) is (-slope, scale)/hypot. Uniform offsets in
    kHz_equivalent are converted back into actual ms and kHz coordinates.
    The global predicted chirp sweep determines the square scale; no labels.
    """
    import numpy as np

    z = np.asarray(curve, dtype=float)
    require(z.ndim == 2 and z.shape[0] >= 3 and z.shape[1] >= 2
            and np.isfinite(z[:, :2]).all()
            and np.all(np.diff(z[:, 0]) > 0),
            "invalid tracked ridge for normal sampling")
    duration_ms = (z[-1, 0] - z[0, 0]) * 1000.
    require(duration_ms > 0., "zero-length trajectory")
    total_sweep_khz = abs(z[-1, 1] - z[0, 1]) / 1000.
    # Stable geometric scale from predicted curve only. This clamps extremes
    # explicitly to keep transverse sampling well-defined near zero slope.
    scale = float(max(3., min(80., total_sweep_khz / duration_ms)))
    t = np.linspace(z[0, 0], z[-1, 0], 72)
    f = np.interp(t, z[:, 0], z[:, 1])
    slope = np.gradient(f / 1000., t * 1000.)
    norm = np.hypot(slope, scale)
    require(np.all(np.isfinite(norm)) and np.all(norm > 0),
            "cannot normalize predicted ridge slope")
    nu = -slope / norm
    nf = scale / norm
    profiles = []
    for offset in OFFSETS:
        shifted_t = t + offset * nu / scale / 1000.
        shifted_f = f + offset * nf * 1000.
        profiles.append(sample_bilinear(ref, shifted_t, shifted_f))
    profiles = np.vstack(profiles).T
    valid = np.all(np.isfinite(profiles), axis=1)
    require(int(valid.sum()) >= 16, "insufficient fully supported perpendicular profiles")
    matrix = profiles[valid]
    # Profile center is index 4, shoulders at +/- 4 kHz_eq (2,6), +/-8 (0,8).
    center = matrix[:, 4]
    side4 = np.maximum(matrix[:, 2], matrix[:, 6])
    side8 = np.maximum(matrix[:, 0], matrix[:, 8])
    contrast4 = center - side4
    contrast8 = center - side8
    # 2 dB tolerance at +/- 2 kHz_eq protects small tracking offsets.
    dominant = center >= np.maximum(matrix[:, 3], matrix[:, 5]) - 2.
    peak_idx = np.argmax(matrix, axis=1)
    peak_offsets = np.abs(np.asarray(OFFSETS)[peak_idx])
    widths = []
    fractions = []
    for row, peak in zip(matrix, peak_idx):
        threshold = float(row[peak]) - 6.
        lo, hi = int(peak), int(peak)
        while lo > 0 and row[lo-1] >= threshold:
            lo -= 1
        while hi < len(OFFSETS)-1 and row[hi+1] >= threshold:
            hi += 1
        widths.append(float(OFFSETS[hi] - OFFSETS[lo]))
        # Difference in dB -> relative power. Clip to avoid overflows.
        power = np.power(10., np.clip(row, -30., 60.) / 10.)
        fractions.append(float(power[3:6].sum() / power.sum()))
    result = {
        "track_sweep_khz": float(total_sweep_khz),
        "normal_time_scale_khz_per_ms": scale,
        "normal_valid_profile_count": int(len(center)),
        "normal_center_snr_median_db": float(np.median(center)),
        "normal_contrast_4khz_median_db": float(np.median(contrast4)),
        "normal_contrast_8khz_median_db": float(np.median(contrast8)),
        "normal_center_dominant_fraction": float(np.mean(dominant)),
        "normal_peak_offset_abs_median_khz_eq": float(np.median(peak_offsets)),
        "normal_6db_width_median_khz_eq": float(np.median(widths)),
        "normal_pm2_energy_fraction_median": float(np.median(fractions)),
    }
    require(all(math.isfinite(float(x)) for x in result.values()),
            "nonfinite transverse profile statistics")
    return result


def score_fixed_gates(rows):
    pos = [r for r in rows if r["cohort"] == "rescued_positive"]
    neg = [r for r in rows if r["cohort"] == "remaining_tracked_artifact"]
    require(len(pos) == 22 and len(neg) == 4, "P2.3 diagnostic cohort mismatch")
    return {
        name: {
            "chirps_retained_of_22": sum(bool(func(r)) for r in pos),
            "chirps_lost_of_22": 22 - sum(bool(func(r)) for r in pos),
            "artifact_tracks_retained_of_4": sum(bool(func(r)) for r in neg),
            "artifact_tracks_rejected_of_4": 4 - sum(bool(func(r)) for r in neg),
            "independent_validation": False,
        }
        for name, func in GATES.items()
    }


def cohorts(rows):
    out = {}
    for label in ("rescued_positive", "remaining_tracked_artifact"):
        group = [x for x in rows if x["cohort"] == label]
        stats = {}
        for name in FEATURES:
            values = sorted(float(x[name]) for x in group)
            stats[name] = {
                "min": values[0],
                "median": float(statistics.median(values)),
                "max": values[-1],
            }
        out[label] = {"count": len(group), "features": stats}
    return out


def compute(args):
    import numpy as np
    import soundfile as sf
    import bat_analysis.detection as detector
    from benchmark import reassignment_detector_seeds as bench
    from benchmark.snr_metrics import ReferenceSNR
    from laboratory.runner import remove_excluded_references

    dataset, processing = args.dataset.resolve(), args.processing.resolve()
    review = args.review_project.resolve()
    require(revision(processing) == DETECTOR_REFERENCE and
            Path(detector.__file__).resolve().is_relative_to(processing) and
            Path(bench.__file__).resolve().is_relative_to(processing),
            "scientific detector checkout differs from pinned P0")
    require(sha256(dataset / "manifest.csv") == MANIFEST_REFERENCE,
            "frozen calibration manifest changed")
    p1 = json.loads(args.p1.read_text(encoding="utf-8"))
    p2 = json.loads((args.p2 / "report.json").read_text(encoding="utf-8"))
    validate_p2(p2, p1, review)
    p22 = json.loads((args.p22 / "report.json").read_text(encoding="utf-8"))
    require(p22["status"] == "complete" and
            p22["dataset_manifest_sha256"] == MANIFEST_REFERENCE and
            p22["p0_detector_sha"] == DETECTOR_REFERENCE and
            p22["p2_experiment_sha"] == P2_SHA and
            p22["p1_decisions_sha256"] == p1["decisions_sha256"] and
            p22["positive_tracks"] == 22 and
            p22["remaining_artifact_tracks"] == 4 and
            p22["heldout_test_wav_json_opened"] is False and
            p22["scientific_merge_authorized"] is False,
            "invalid P2.2 scientific provenance")
    p22cases = csv_rows(args.p22 / "case_spectral_metrics.csv")
    require(len(p22cases) == 26 and
            len({row["case_id"] for row in p22cases}) == 26 and
            sum(row["cohort"] == "rescued_positive" for row in p22cases) == 22 and
            sum(row["cohort"] == "remaining_tracked_artifact" for row in p22cases) == 4,
            "P2.2 detailed membership changed")
    review_cases = json.loads((review / "review_cases.json").read_text(encoding="utf-8"))
    review_by_id = {row["case_id"]: row for row in review_cases}
    require(len(review_by_id) == 35 and
            set(row["case_id"] for row in p22cases).issubset(review_by_id),
            "P1 case metadata was altered")
    manifest_rows = csv_rows(dataset / "manifest.csv")
    manifest = {r["dataset_wav"]: r for r in manifest_rows if r["partition"] == "calibration"}
    require(len(manifest) == 428, "unexpected calibration manifest")
    groups = defaultdict(list)
    for row in p22cases:
        groups[row["wav"]].append(row)
    require(len(groups) == p22["reviewed_wavs"], "P2.2 reviewed WAV cohort changed")
    results = []
    for count, (name, rows) in enumerate(sorted(groups.items()), 1):
        metadata = manifest.get(name)
        require(metadata and name.startswith("calibration/wav/"),
                "P2.3 case is not in calibration")
        wav = checked_path(dataset, name, "calibration/wav/")
        ann = checked_path(dataset, metadata["dataset_json"],
                           "calibration/annotations/")
        require(sha256(wav).lower() == metadata["wav_sha256"].lower()
                and sha256(ann).lower() == metadata["json_sha256"].lower(),
                "frozen calibration files changed")
        doc = json.loads(ann.read_text(encoding="utf-8-sig"))
        record = remove_excluded_references({name: doc["files"][wav.name]})[0][name]
        manual = bench._manual_annotations(record)
        negative = rows[0]["cohort"] == "remaining_tracked_artifact"
        require((negative and record.get("review_status") == "no_chirp" and not manual)
                or (not negative and manual and
                    record.get("review_status") != "no_chirp"),
                "positive or no_chirp annotation integrity mismatch")
        y, sr = sf.read(wav, always_2d=False)
        if y.ndim == 2:
            y = np.mean(y, axis=1)
        baseline, hybrid, extras = hybrid_candidates(y, int(sr), detector)
        matches = ({j: i for i, j in bench.match_candidates(
            hybrid, manual, geometry_mode="square_normals_v1")}
            if not negative else {})
        ref = ReferenceSNR.from_audio(y, int(sr))
        used = set()
        for row in rows:
            case = review_by_id[row["case_id"]]
            require(case["input_dataset_wav"] == name and
                    row["cohort"] == (
                        "remaining_tracked_artifact" if
                        case["review_category"] == "review_candidate_on_no_chirp_wav"
                        else "rescued_positive"), "review label or source changed")
            index, candidate = find_extra(case, extras)
            require(index not in used, "duplicated reviewed candidate")
            used.add(index)
            if not negative:
                js = [j for j, entry in enumerate(manual)
                      if str(entry[0]) == str(case["manual_chirp_id"])]
                require(len(js) == 1 and js[0] in matches and
                        matches[js[0]] == len(baseline) + index,
                        "P2.3 selected seed differs from annotated rescue")
            curve = track(y, int(sr), candidate, bench,
                          {"n_fft": None, "hop": None,
                           "search_margin_ms": None, "trim_echo_rebound": True})
            require(curve is not None, "P2.2 previously tracked seed lost")
            geom = curve_features(curve, candidate)
            require(math.isclose(geom["track_frequency_drop_khz"],
                                 float(row["track_sweep_khz"]),
                                 rel_tol=1e-7, abs_tol=1e-5) and
                    math.isclose(geom["track_duration_ms"],
                                 float(row["track_duration_ms"]),
                                 rel_tol=1e-7, abs_tol=1e-5) and
                    geom["track_frequency_drop_khz"] >= 8.0,
                    "P2.2 geometry drift or wrong 8kHz cohort")
            features = normal_profiles(curve, ref)
            results.append({
                "case_id": case["case_id"], "wav": name,
                "cohort": row["cohort"],
                "review_snr_group": row["review_snr_group"],
                **features,
            })
        if count % 5 == 0 or count == len(groups):
            print("P23_PROGRESS: reviewed_wavs=" + str(count)
                  + "/" + str(len(groups)), flush=True)
    require(len(results) == 26 and
            len({r["case_id"] for r in results}) == 26,
            "P2.3 normal diagnostic missing a case")
    gates = score_fixed_gates(results)
    summary = {
        "schema_version": 1, "status": "complete",
        "scope": "P2.3 predicted-track transverse STFT; calibration development only",
        "dataset_manifest_sha256": MANIFEST_REFERENCE,
        "p0_detector_sha": DETECTOR_REFERENCE,
        "p2_experiment_sha": P2_SHA,
        "p1_decisions_sha256": p1["decisions_sha256"],
        "positive_tracks": 22, "remaining_artifact_tracks": 4,
        "reviewed_wavs": len(groups), "normal_offsets_khz_eq": list(OFFSETS),
        "measurement": "predicted-normal fixed flattop STFT, no manual geometry at inference",
        "cohort_distributions": cohorts(results),
        "fixed_gate_results": gates,
        "same_calibration_zero_loss_hypotheses": [
            name for name, value in gates.items()
            if value["chirps_lost_of_22"] == 0 and
            value["artifact_tracks_rejected_of_4"] > 0
        ],
        "independent_validation_done": False,
        "heldout_test_wav_json_opened": False,
        "scientific_merge_authorized": False,
        "limitations": (
            "26 P1-reviewed examples used in previous hypothesis formation; "
            "not independent. Positive files incompletely annotated; no "
            "unlabeled positives called negatives. No full 493-reference "
            "or 203-negative WAV nonregression measured by these case gates. "
            "Ridge concentration describes energy, not biological truth. "
            "Candidate thresholds are descriptive; no deployment permitted."
        ),
    }
    return summary, results


def main():
    parser = argparse.ArgumentParser()
    for name in ("dataset", "p1", "p2", "p22", "review-project",
                 "processing", "output"):
        parser.add_argument("--" + name, type=Path, required=True)
    args = parser.parse_args()
    output = args.output.resolve()
    require(not output.exists() and not output.is_relative_to(args.dataset.resolve())
            and not output.is_relative_to(args.review_project.resolve())
            and not output.is_relative_to(args.p2.resolve())
            and not output.is_relative_to(args.p22.resolve()),
            "P2.3 output must be new and separate from all inputs")
    summary, rows = compute(args)
    output.mkdir(parents=True, exist_ok=False)
    fields = ("case_id", "wav", "cohort", "review_snr_group", *FEATURES)
    with (output / "normal_profile_cases.csv").open(
        "w", encoding="utf-8", newline="") as f:
        writer = csv.DictWriter(f, fieldnames=fields)
        writer.writeheader()
        writer.writerows(rows)
    (output / "report.json").write_text(
        json.dumps(summary, ensure_ascii=False, indent=2, allow_nan=False) + "\n",
        encoding="utf-8")
    print("P23_NORMAL_RESULT: " + json.dumps({
        "status": summary["status"],
        "positive_tracks": 22, "negative_tracks": 4,
        "gates": summary["fixed_gate_results"],
        "zero_loss_hypotheses": summary["same_calibration_zero_loss_hypotheses"],
        "scientific_merge_authorized": False,
    }, ensure_ascii=False, separators=(",", ":")), flush=True)


if __name__ == "__main__":
    main()
