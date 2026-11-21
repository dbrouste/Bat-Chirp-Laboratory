"""P2.2: fixed-STFT spectral evidence for four remaining tracked artifacts.

Exploratory matched analysis of the same 22 rescued manually confirmed chirps
and 4 negative tracker proposals retained after P2.1 frequency-sweep >=8 kHz.
These are calibration *development* cases, never independent validation.

We use the exact reference SNR transform (Flattop 512, hop 64, percentile 50)
and only the detector's own curves as sampling locations. Manual annotations
never determine spectral samples, thresholds, or trajectories. Stationary-line
and delayed-ridge statistics are *proxies*, NOT proof of interference or echo.
No detector/annotation/test data is modified, and no merge is authorized.
"""
from __future__ import annotations

import argparse
import csv
import json
import math
import statistics
from collections import Counter, defaultdict
from pathlib import Path

from experiments.p21_artifact_diagnostics import (
    DETECTOR_REFERENCE, LAB_REFERENCE, MANIFEST_REFERENCE, P2_SHA,
    checked_path, csv_rows, curve_features, find_extra, hybrid_candidates,
    require, revision, sha256, track, validate_p2,
)

SWEEP_MIN_KHZ = 8.0
# Defined BEFORE examining these spectral features. These are descriptive
# hypotheses only; thresholds must not be selected on these same reviewed cases
# as if they were independently validated production rules.
DIAGNOSTIC_GATES = {
    "median_track_snr_ge_6db": lambda f: f["snr_median_db"] >= 6.0,
    "track_support_6db_ge_60pct": lambda f: f["snr_support_6db_fraction"] >= 0.60,
    "track_support_10db_ge_50pct": lambda f: f["snr_support_10db_fraction"] >= 0.50,
    "flank_contrast_ge_3db": lambda f: f["flank_contrast_median_db"] >= 3.0,
    "stationary_tone_occupancy_le_15pct": lambda f: (
        f["stationary_occupancy_10db_fraction"] is not None
        and f["stationary_occupancy_10db_fraction"] <= 0.15),
    "delayed_same_frequency_support_le_50pct": lambda f: (
        f["delayed_same_frequency_support_max_fraction"] is not None
        and f["delayed_same_frequency_support_max_fraction"] <= 0.50),
}
FEATURE_KEYS = (
    "valid_spectral_samples", "snr_p10_db", "snr_median_db",
    "snr_p90_db", "snr_support_6db_fraction",
    "snr_support_10db_fraction", "flank_contrast_median_db",
    "stationary_occupancy_10db_fraction",
    "delayed_same_frequency_support_max_fraction",
)


def spectral_features(curve, ref):
    """Measure along the tracked ridge, with no ground-truth samples.

    A separate fixed ordinary STFT is used; reassignment tracker output is
    NOT its own spectral evidence. Out-of-STFT-support timestamps are removed
    rather than silently extrapolated. Flanks are +/- 8 kHz, the occupancy
    excludes the event +/- 5 ms; delayed same-frequency tests have 0.5/1/2 ms
    lags and must be read as echo *proxies*, not source identifications.
    """
    import numpy as np

    arr = np.asarray(curve, dtype=float)
    require(arr.ndim == 2 and arr.shape[0] >= 2 and arr.shape[1] >= 2
            and np.isfinite(arr[:, :2]).all()
            and np.all(np.diff(arr[:, 0]) > 0),
            "invalid P2.2 tracker output")
    grid_t = np.linspace(arr[0, 0], arr[-1, 0], 80)
    grid_f = np.interp(grid_t, arr[:, 0], arr[:, 1])
    max_f = float(ref.freqs_hz[-1])
    keep = ((grid_t >= ref.times_s[0]) & (grid_t <= ref.times_s[-1])
            & (grid_f >= 0.0) & (grid_f <= max_f))
    require(int(np.count_nonzero(keep)) >= 12,
            "too few in-range STFT samples for P2.2")
    t, f = grid_t[keep], grid_f[keep]
    snr = np.asarray(ref.along_reference(t, f), dtype=float)
    require(len(snr) == len(t) and np.isfinite(snr).all(),
            "nonfinite fixed-STFT track SNR")

    # A flank comparison is only computed where both sidebands are valid.
    flank_keep = (f >= 8000.0) & (f <= max_f - 8000.0)
    require(int(flank_keep.sum()) >= 8,
            "insufficient two-sided flanks at valid frequencies")
    a = np.asarray(ref.along_reference(t[flank_keep], f[flank_keep]-8000.0))
    b = np.asarray(ref.along_reference(t[flank_keep], f[flank_keep]+8000.0))
    ridge = snr[flank_keep]
    contrast = float(np.median(ridge - np.maximum(a, b)))

    # Persistence at the *same* frequencies outside the event window. This
    # detects possible stationary lines, but can also see background chirps.
    bg = (ref.times_s < grid_t[0] - 0.005) | (ref.times_s > grid_t[-1] + 0.005)
    occupancy = None
    if int(bg.sum()) >= 10:
        df = float(ref.freqs_hz[1] - ref.freqs_hz[0])
        f_indices = np.unique(np.clip(np.rint(f[::4] / df).astype(int),
                                      0, len(ref.freqs_hz) - 1))
        occupancy = float(np.mean(ref.snr_db[f_indices][:, bg] >= 10.0))

    # A delayed same-frequency component can be an echo, repeated calling,
    # stationary interference or another source; never label it automatically.
    delayed = []
    for lag in (0.0005, 0.001, 0.002):
        valid = t + lag <= ref.times_s[-1]
        if int(valid.sum()) >= 8:
            later = ref.along_reference(t[valid]+lag, f[valid])
            delayed.append(float(np.mean(np.asarray(later) >= 10.0)))

    result = {
        "valid_spectral_samples": int(len(t)),
        "snr_p10_db": float(np.percentile(snr, 10.0)),
        "snr_median_db": float(np.median(snr)),
        "snr_p90_db": float(np.percentile(snr, 90.0)),
        "snr_support_6db_fraction": float(np.mean(snr >= 6.0)),
        "snr_support_10db_fraction": float(np.mean(snr >= 10.0)),
        "flank_contrast_median_db": contrast,
        "stationary_occupancy_10db_fraction": occupancy,
        "delayed_same_frequency_support_max_fraction": (
            max(delayed) if delayed else None),
    }
    require(all(v is None or math.isfinite(v) for v in result.values()),
            "nonfinite spectral diagnostic")
    return result


def distribution(rows):
    out = {}
    for cohort in ("rescued_positive", "remaining_tracked_artifact"):
        subset = [r for r in rows if r["cohort"] == cohort]
        vals = {}
        for key in FEATURE_KEYS:
            numbers = sorted(float(r[key]) for r in subset if r.get(key) is not None)
            vals[key] = (
                {"n": len(numbers), "min": numbers[0],
                 "median": statistics.median(numbers),
                 "max": numbers[-1]}
                if numbers else {"n": 0})
        out[cohort] = {"count": len(subset), "features": vals}
    return out


def score_predeclared_gates(rows):
    positives = [r for r in rows if r["cohort"] == "rescued_positive"]
    negatives = [r for r in rows if r["cohort"] == "remaining_tracked_artifact"]
    require(len(positives) == 22 and len(negatives) == 4,
            "P2.2 gate cohorts are not 22 vs 4")
    report = {}
    for label, func in DIAGNOSTIC_GATES.items():
        good = sum(bool(func(x)) for x in positives)
        bad = sum(bool(func(x)) for x in negatives)
        report[label] = {
            "rescued_chirps_retained_of_22": good,
            "rescued_chirps_lost_of_22": 22-good,
            "remaining_artifact_tracks_retained_of_4": bad,
            "remaining_artifact_tracks_rejected_of_4": 4-bad,
            "same_calibration_only": True,
            "independent_validation": False,
        }
    return report


def compute(args):
    import numpy as np
    import soundfile as sf
    import bat_analysis.detection as detector
    from benchmark import reassignment_detector_seeds as bench
    from benchmark.snr_metrics import ReferenceSNR
    from laboratory.runner import remove_excluded_references

    dataset = args.dataset.resolve()
    review = args.review_project.resolve()
    processing = args.processing.resolve()
    p21_dir = args.p21.resolve()
    require(revision(processing) == DETECTOR_REFERENCE
            and Path(detector.__file__).resolve().is_relative_to(processing)
            and Path(bench.__file__).resolve().is_relative_to(processing),
            "P0 pinned detector module changed")
    require(sha256(dataset / "manifest.csv") == MANIFEST_REFERENCE,
            "frozen calibration corpus drifted")
    p1 = json.loads(args.p1.read_text(encoding="utf-8"))
    p2 = json.loads((args.p2 / "report.json").read_text(encoding="utf-8"))
    validate_p2(p2, p1, review)
    p21 = json.loads((p21_dir / "report.json").read_text(encoding="utf-8"))
    require(p21["status"] == "complete" and
            p21["scope"].startswith("P2.1 exploratory") and
            p21["p0_detector_sha"] == DETECTOR_REFERENCE and
            p21["p0_lab_sha"] == LAB_REFERENCE and
            p21["p2_experiment_sha"] == P2_SHA and
            p21["dataset_manifest_sha256"] == MANIFEST_REFERENCE and
            p21["p1_decisions_sha256"] == p1["decisions_sha256"] and
            p21["reviewed_wavs"] == 22 and
            p21["rescued_chirps"] == 22 and
            p21["artifact_candidates"] == 13 and
            p21["artifact_candidates_tracked"] == 7 and
            p21["artifact_candidates_rejected_by_tracker"] == 6 and
            p21["scientific_merge_authorized"] is False and
            p21["heldout_test_wav_json_opened"] is False and
            p21["fixed_diagnostic_gates"]["track_sweep_ge_8khz"][
                "rescued_chirps_retained_of_22"] == 22 and
            p21["fixed_diagnostic_gates"]["track_sweep_ge_8khz"][
                "extra_negative_tracks_retained_of_7"] == 4,
            "P2.1 pinned diagnostic report does not match P2.2 prerequisites")
    source_cases = csv_rows(p21_dir / "case_features.csv")
    require(len(source_cases) == 35 and
            len({x["case_id"] for x in source_cases}) == 35, "P2.1 cases missing")
    review_cases = json.loads((review / "review_cases.json").read_text(encoding="utf-8-sig"))
    require(len(review_cases) == 35 and
            {x["case_id"] for x in review_cases} ==
            {x["case_id"] for x in source_cases}, "P1/P2.1 case identities differ")
    p21_by_id = {x["case_id"]: x for x in source_cases}
    selected = []
    for item in review_cases:
        source = p21_by_id[item["case_id"]]
        positive = item["review_category"] == "review_manually_annotated_chirp"
        negative = item["review_category"] == "review_candidate_on_no_chirp_wav"
        require((positive and not item["review_only_marker"])
                or (negative and item["review_only_marker"]),
                "review label category ambiguous")
        require(source["wav"] == item["input_dataset_wav"] and
                source["label"] == ("validated_chirp" if positive else "reviewed_artifact")
                and source["tracked"] in ("True", "False"),
                "P2.1 label or WAV mapping altered")
        sweep = float(source["track_frequency_drop_khz"]) if source["tracked"] == "True" else None
        if positive:
            require(source["tracked"] == "True" and sweep >= SWEEP_MIN_KHZ,
                    "one rescued true chirp lost by P2.1 sweep")
        if source["tracked"] == "True" and sweep >= SWEEP_MIN_KHZ:
            selected.append(item)
    require(len(selected) == 26 and sum(
        x["review_category"] == "review_manually_annotated_chirp"
        for x in selected) == 22, "P2.2 did not isolate 22 positives and 4 negatives")
    require(sum(x["tracked"] == "True" and
                float(x["track_frequency_drop_khz"]) < SWEEP_MIN_KHZ
                for x in source_cases if x["label"] == "reviewed_artifact") == 3,
            "P2.1 8kHz cutoff did not reject exactly three false proposals")
    all_manifest = csv_rows(dataset / "manifest.csv")
    manifest = {x["dataset_wav"]: x for x in all_manifest if x["partition"] == "calibration"}
    require(len(manifest) == 428, "calibration manifest membership changed")
    groups = defaultdict(list)
    for case in selected:
        groups[case["input_dataset_wav"]].append(case)
    rows = []
    for index, (name, group) in enumerate(sorted(groups.items()), 1):
        record_meta = manifest.get(name)
        require(record_meta and name.startswith("calibration/wav/"),
                "unknown calibration WAV in spectral analysis")
        wav = checked_path(dataset, name, "calibration/wav/")
        ann = checked_path(dataset, record_meta["dataset_json"],
                           "calibration/annotations/")
        require(sha256(wav).lower() == record_meta["wav_sha256"].lower()
                and sha256(ann).lower() == record_meta["json_sha256"].lower(),
                "frozen original data were modified")
        document = json.loads(ann.read_text(encoding="utf-8-sig"))
        recording = remove_excluded_references(
            {name: document["files"][wav.name]})[0][name]
        manual = bench._manual_annotations(recording)
        positive_group = group[0]["review_category"] == "review_manually_annotated_chirp"
        require((positive_group and bool(manual) and recording.get("review_status") != "no_chirp")
                or (not positive_group and not manual
                    and recording.get("review_status") == "no_chirp"),
                "negative and positive reviewed cohorts mixed")
        y, sr = sf.read(wav, always_2d=False)
        if y.ndim == 2:
            y = np.mean(y, axis=1)
        baseline, hybrid, extras = hybrid_candidates(y, int(sr), detector)
        fixed = ReferenceSNR.from_audio(y, int(sr))  # Exactly P0 STFT defaults.
        matches = ({j: i for i, j in bench.match_candidates(
            hybrid, manual, geometry_mode="square_normals_v1")}
            if positive_group else {})
        used = set()
        for case in group:
            i, candidate = find_extra(case, extras)
            require(i not in used, "duplicate candidate case association")
            used.add(i)
            if positive_group:
                indices = [j for j, m in enumerate(manual)
                           if str(m[0]) == str(case["manual_chirp_id"])]
                require(len(indices) == 1 and indices[0] in matches and
                        matches[indices[0]] == len(baseline) + i,
                        "P2.2 would score a different recovered chirp")
            curve = track(y, int(sr), candidate, bench, {
                "n_fft": None, "hop": None, "search_margin_ms": None,
                "trim_echo_rebound": True,
            })
            require(curve is not None, "P2.1 tracked curve disappeared")
            geo = curve_features(curve, candidate)
            prior = p21_by_id[case["case_id"]]
            require(math.isclose(float(geo["track_frequency_drop_khz"]),
                                 float(prior["track_frequency_drop_khz"]),
                                 rel_tol=1e-7, abs_tol=1e-5)
                    and math.isclose(float(geo["track_duration_ms"]),
                                     float(prior["track_duration_ms"]),
                                     rel_tol=1e-7, abs_tol=1e-5),
                    "P2.1 tracker curve geometry has drifted")
            require(geo["track_frequency_drop_khz"] >= SWEEP_MIN_KHZ,
                    "tracked P2.1 seed no longer passes the 8 kHz gate")
            spectral = spectral_features(curve, fixed)
            rows.append({
                "case_id": case["case_id"],
                "wav": name,
                "cohort": "rescued_positive" if positive_group else "remaining_tracked_artifact",
                "review_snr_group": "high" if positive_group and
                    case["review_priority"] == 1 else "medium" if positive_group else "none",
                "track_duration_ms": float(geo["track_duration_ms"]),
                "track_sweep_khz": float(geo["track_frequency_drop_khz"]),
                **spectral,
            })
        if index % 5 == 0 or index == len(groups):
            print("P22_PROGRESS: reviewed_wavs=" + str(index) + "/" +
                  str(len(groups)), flush=True)
    require(len(rows) == 26 and len({x["case_id"] for x in rows}) == 26,
            "P2.2 missing case-level results")
    gate = score_predeclared_gates(rows)
    summary = {
        "schema_version": 1,
        "status": "complete",
        "scope": "P2.2 frozen calibration only; 22 rescued positives vs 4 reviewed false tracks",
        "dataset_manifest_sha256": MANIFEST_REFERENCE,
        "p0_detector_sha": DETECTOR_REFERENCE,
        "p2_experiment_sha": P2_SHA,
        "p1_decisions_sha256": p1["decisions_sha256"],
        "p21_sweep_threshold_khz": SWEEP_MIN_KHZ,
        "reviewed_wavs": len(groups),
        "positive_tracks": 22,
        "remaining_artifact_tracks": 4,
        "already_rejected_negative_tracks_by_8khz": 3,
        "other_negative_candidates_untracked": 6,
        "spectral_transform": "fixed non-reassigned STFT flattop n_fft=512 hop=64 median per-frequency dB noise",
        "cohort_distributions": distribution(rows),
        "predeclared_diagnostic_gates": gate,
        "same_calibration_zero_loss_diagnostics": [
            name for name, outcome in gate.items()
            if outcome["rescued_chirps_lost_of_22"] == 0
            and outcome["remaining_artifact_tracks_rejected_of_4"] > 0
        ],
        "echo_or_interference_confirmed": False,
        "independent_validation_done": False,
        "heldout_test_wav_json_opened": False,
        "scientific_merge_authorized": False,
        "methodological_notes": (
            "All four negatives and 22 positives are P1-labeled calibration cases "
            "already used to formulate the morphology/sweep hypothesis. STFT support "
            "is a diagnostic, not a new biological annotation. Background-frequency "
            "persistence and delayed same-frequency response are proxies and cannot "
            "prove source as echo or stationary interference. Positive WAV annotations "
            "are incomplete; only explicitly reviewed no_chirp WAVs are negatives. "
            "Every reported threshold is exploratory; full 493-reference / 203-negative "
            "nonregression and independent validation are required before deployment."
        ),
    }
    return summary, rows


def main():
    p = argparse.ArgumentParser()
    for name in ("dataset", "p1", "p2", "p21", "review-project",
                 "processing", "output"):
        p.add_argument("--" + name, type=Path, required=True)
    args = p.parse_args()
    output = args.output.resolve()
    require(not output.exists() and not output.is_relative_to(args.dataset.resolve())
            and not output.is_relative_to(args.review_project.resolve())
            and not output.is_relative_to(args.p2.resolve())
            and not output.is_relative_to(args.p21.resolve()),
            "P2.2 cannot overwrite scientific or review inputs")
    summary, rows = compute(args)
    output.mkdir(parents=True, exist_ok=False)
    fields = ("case_id", "wav", "cohort", "review_snr_group",
              "track_duration_ms", "track_sweep_khz", *FEATURE_KEYS)
    with (output / "case_spectral_metrics.csv").open(
        "w", newline="", encoding="utf-8") as stream:
        writer = csv.DictWriter(stream, fieldnames=fields)
        writer.writeheader()
        writer.writerows(rows)
    (output / "report.json").write_text(
        json.dumps(summary, indent=2, allow_nan=False, ensure_ascii=False) + "\n",
        encoding="utf-8")
    compact = {
        "status": "complete", "positives": 22, "remaining_negatives": 4,
        "same_calibration_zero_loss": summary["same_calibration_zero_loss_diagnostics"],
        "fixed_gates": summary["predeclared_diagnostic_gates"],
        "scientific_merge_authorized": False,
    }
    print("P22_SPECTRAL_RESULT: " + json.dumps(
        compact, separators=(",", ":"), ensure_ascii=False), flush=True)


if __name__ == "__main__":
    main()
