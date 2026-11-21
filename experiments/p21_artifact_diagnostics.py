"""P2.1: classify TRACKED extra artifacts vs rescued real chirps, read-only.

Scientific scope: descriptive calibration-only screen. The 35 labels have already
been reviewed by a human in P1. No test WAV/JSON, no original paths, no detector
or annotation writes. Rules below are fixed diagnostic hypotheses, NOT trained
thresholds, and must NOT be used as a production filter without new validation.
"""
from __future__ import annotations

import argparse
import csv
import json
import math
import statistics
from collections import Counter, defaultdict
from pathlib import Path

from experiments.p2_hybrid_trajectory import (
    DETECTOR_REFERENCE, LAB_REFERENCE, MANIFEST_REFERENCE, hybrid_candidates,
    identity, require, revision, sha256,
)

P2_SHA = "0f15f11973b5e9c17444a8805a37152a0cfce197"
METRICS = (
    "track_duration_ms", "track_frequency_drop_khz", "track_max_step_khz",
    "track_gap_ratio", "track_points", "duration_over_blob_ratio",
    "candidate_blob_width_ms", "candidate_blob_span_khz", "candidate_peak_db",
    "candidate_slope_khz_per_ms",
)

# Hypotheses defined before viewing P2.1 per-candidate results. Selection from
# this same calibration dataset MUST NOT be called independent validation.
RULES = {
    "track_duration_ge_1ms": lambda f: f["track_duration_ms"] >= 1.0,
    "track_sweep_ge_8khz": lambda f: f["track_frequency_drop_khz"] >= 8.0,
    "duration1_and_sweep8": lambda f: (
        f["track_duration_ms"] >= 1.0 and f["track_frequency_drop_khz"] >= 8.0),
    "duration1p5_and_sweep12": lambda f: (
        f["track_duration_ms"] >= 1.5 and f["track_frequency_drop_khz"] >= 12.0),
    "track_gap_ratio_le_3": lambda f: f["track_gap_ratio"] <= 3.0,
}


def csv_rows(path):
    with path.open(encoding="utf-8-sig", newline="") as handle:
        reader = csv.DictReader(handle)
        names = reader.fieldnames or []
        require(names and len(names) == len(set(names)), "invalid P2 input CSV")
        rows = list(reader)
    require(all(None not in row and all(v is not None for v in row.values()) for row in rows),
            "malformed P2 input CSV")
    return rows


def checked_path(root, text, prefix):
    require(isinstance(text, str) and text.startswith(prefix) and
            ":" not in text and "\\" not in text and
            not any(x in (".", "..") for x in Path(text).parts),
            "invalid calibration-relative dataset path")
    path = (root / text).resolve()
    require(path.is_relative_to(root) and path.is_file(), "frozen input not found")
    return path


def curve_features(curve, candidate):
    """All shape data derive from detector output, NEVER from manual annotations."""
    import numpy as np

    if curve is None:
        return None
    z = np.asarray(curve, dtype=float)
    require(z.ndim == 2 and z.shape[0] >= 2 and z.shape[1] >= 2,
            "non-trajectory returned by pinned tracker")
    require(np.isfinite(z[:, :2]).all(), "nonfinite track time or frequency")
    dt = np.diff(z[:, 0])
    require(np.all(dt > 0), "nonmonotonic track timestamps")
    gaps = float(np.max(dt) / np.median(dt))
    duration = 1000.0 * float(z[-1, 0] - z[0, 0])
    drop = float((z[0, 1] - z[-1, 1]) / 1000.0)
    step = float(np.max(np.abs(np.diff(z[:, 1]))) / 1000.0)
    width = float(candidate["width_ms"])
    vals = {
        "track_duration_ms": duration,
        "track_frequency_drop_khz": drop,
        "track_max_step_khz": step,
        "track_gap_ratio": gaps,
        "track_points": int(len(z)),
        "duration_over_blob_ratio": duration / width if width > 0 else None,
        "candidate_blob_width_ms": width,
        "candidate_blob_span_khz": float(candidate["height_hz"]) / 1000.0,
        "candidate_peak_db": float(candidate["peak_db"]),
        "candidate_slope_khz_per_ms": float(candidate["slope_hz_per_ms"]) / 1000.0,
    }
    require(all(v is None or math.isfinite(v) for v in vals.values()),
            "nonfinite diagnostic geometry")
    return vals


def find_extra(case, extras):
    """One-to-one identification using P1 exported candidate geometry, no labels."""
    hits = [
        (i, c) for i, c in enumerate(extras)
        if abs(1000.0 * float(c["time_mid"]) -
               float(case["candidate_peak_time_ms"])) <= 0.01
        and abs(float(c["peak_freq_hz"]) / 1000.0 -
                float(case["candidate_peak_freq_khz"])) <= 0.01
        and abs(float(c["width_ms"]) - float(case["blob_width_ms"])) <= 0.01
        and abs(float(c["height_hz"]) / 1000.0 -
                float(case["blob_frequency_span_khz"])) <= 0.01
    ]
    require(len(hits) == 1, "P1 candidate case is not uniquely reproducible")
    return hits[0]


def track(y, sr, candidate, bench, options):
    diagnostics = {}
    kwargs = ({"search_margin_ms": options["search_margin_ms"]}
              if options["search_margin_ms"] is not None else {})
    return bench.track_reassigned_candidate(
        y, sr, candidate, n_fft=options["n_fft"], hop=options["hop"],
        use_broad_seed=True, complete_ends=True,
        trim_echo_rebound=options["trim_echo_rebound"],
        diagnostics=diagnostics, **kwargs)


def gate_table(rows):
    positives = [r for r in rows if r["label"] == "validated_chirp"]
    artifacts = [r for r in rows if r["label"] == "reviewed_artifact" and r["tracked"]]
    require(len(positives) == 22 and len(artifacts) == 7,
            "gate cohorts cannot be scored on unexpected membership")
    require(all(r["tracked"] and r["features"] is not None for r in positives),
            "lost tracked reference")
    result = {}
    for key, func in RULES.items():
        pos_keep = sum(bool(func(r["features"])) for r in positives)
        neg_keep = sum(bool(func(r["features"])) for r in artifacts)
        result[key] = {
            "rescued_chirps_retained_of_22": pos_keep,
            "rescued_chirps_rejected_of_22": 22 - pos_keep,
            "extra_negative_tracks_retained_of_7": neg_keep,
            "extra_negative_tracks_suppressed_of_7": 7 - neg_keep,
            "on_same_calibration_review_only": True,
            "independent_validation": False,
        }
    return result


def distribution(rows):
    cohorts = {
        "rescued_positives": [r for r in rows if r["label"] == "validated_chirp" and r["tracked"]],
        "tracked_negatives": [r for r in rows if r["label"] == "reviewed_artifact" and r["tracked"]],
        "untracked_negatives": [r for r in rows if r["label"] == "reviewed_artifact" and not r["tracked"]],
    }
    out = {}
    for cohort, subset in cohorts.items():
        d = {"count": len(subset)}
        for key in METRICS:
            vals = [float(r["features"][key]) for r in subset
                    if r["features"] is not None and r["features"].get(key) is not None]
            d[key] = ({
                "n": len(vals), "min": min(vals), "median": statistics.median(vals),
                "max": max(vals)}
                if vals else {"n": 0})
        out[cohort] = d
    return out


def validate_p2(p2, p1, review):
    require(p2["status"] == "complete" and
            p2["dataset_manifest_sha256"] == MANIFEST_REFERENCE and
            p2["p0_lab_sha"] == LAB_REFERENCE and
            p2["p0_detector_sha"] == DETECTOR_REFERENCE and
            p2["p2_experiment_sha"] == P2_SHA and
            p2["p1_human_decisions_sha256"] == p1["decisions_sha256"],
            "P2 baseline and review provenance mismatch")
    require(p2["positive_annotations_exhaustive"] is False and
            p2["geometric_tolerances_calibrated"] is False and
            p2["gate"]["authorized_for_scientific_merge"] is False and
            p2["calibration_wavs"] == 428 and
            p2["positive_wavs"] == 37 and
            p2["reviewed_negative_wavs"] == 203 and
            p2["manual_references"] == 493 and
            p2["baseline"]["matched"] == 401 and
            p2["hybrid"]["matched"] == 423 and
            p2["differences"]["recovered_chirps"] == 22 and
            p2["differences"]["lost_chirps"] == 0 and
            p2["differences"]["added_negative_candidates"] == 13 and
            p2["differences"]["added_negative_tracked_proposals"] == 7,
            "unexpected P2 cohort or scientific status")
    require(p1["status"] == "passed" and p1["review_complete"] is True and
            p1["reviewed"] == 35 and p1["pending"] == 0 and
            p1["test_wav_json_opened"] is False and
            p1["human_decisions_modified"] is False and
            p1["manifest_sha256"] == MANIFEST_REFERENCE and
            p1["cohorts"]["positive_high"]["positive_confirmed"] == 6 and
            p1["cohorts"]["positive_medium"]["positive_confirmed"] == 16 and
            p1["cohorts"]["negative"]["negative_artifact_or_echo"] == 13 and
            p1["cohorts"]["negative"]["negative_true_chirp_label_conflict"] == 0,
            "human review provenance invalid")
    require(sha256(review / "human_decisions.csv") == p1["decisions_sha256"],
            "human decisions changed since P1 audit")
    manifest = json.loads((review / "review_manifest.json").read_text(encoding="utf-8"))
    require(manifest["schema"] == "scientific_error_review_v1" and
            manifest["cohort"] == "development" and
            manifest["calibration_manifest_sha256"] == MANIFEST_REFERENCE and
            manifest["cases"] == 35, "review project provenance invalid")
    require(sha256(review / "review_cases.json") ==
            manifest["files"]["review_cases.json"], "P1 case index changed")


def compute(args):
    import soundfile as sf
    import numpy as np
    import bat_analysis.detection as detector
    from benchmark import reassignment_detector_seeds as bench
    from laboratory.runner import remove_excluded_references

    dataset, review, p2_root = (args.dataset.resolve(),
                               args.review_project.resolve(), args.p2.resolve())
    require(revision(args.processing.resolve()) == DETECTOR_REFERENCE,
            "unpinned detector source")
    require(Path(detector.__file__).resolve().is_relative_to(args.processing.resolve())
            and Path(bench.__file__).resolve().is_relative_to(args.processing.resolve()),
            "unexpected scientific detector module")
    require(sha256(dataset / "manifest.csv") == MANIFEST_REFERENCE,
            "frozen manifest drifted")
    p1 = json.loads(args.p1.read_text(encoding="utf-8"))
    p2 = json.loads((p2_root / "report.json").read_text(encoding="utf-8"))
    validate_p2(p2, p1, review)
    pairs = csv_rows(p2_root / "positive_trajectory_pairs.csv")
    neg = csv_rows(p2_root / "negative_tracked_proposals.csv")
    require(len(pairs) == 493 and len(neg) == 203, "P2 detailed outputs incomplete")
    require(len({(r["file"], r["chirp_id"]) for r in pairs}) == 493,
            "duplicate positive reference in P2")
    recovered = {(r["file"], r["chirp_id"]) for r in pairs
                 if r["baseline_matched"] == "False" and r["hybrid_matched"] == "True"}
    require(len(recovered) == 22 and
            all(r["hybrid_tracked"] == "True" for r in pairs
                if (r["file"], r["chirp_id"]) in recovered), "P2 positives changed")
    require(not any(r["baseline_matched"] == "True" and r["hybrid_matched"] == "False"
                    for r in pairs), "lost reference detected in P2")
    neg_rows = {r["wav"]: r for r in neg}
    require(len(neg_rows) == 203 and
            sum(int(r["added_candidates"]) for r in neg) == 13 and
            sum(int(r["added_tracked_proposals"]) for r in neg) == 7,
            "P2 negative per-WAV results disagree")
    cases = json.loads((review / "review_cases.json").read_text(encoding="utf-8"))
    require(len(cases) == 35 and len({c["case_id"] for c in cases}) == 35,
            "P1 review case identity mismatch")
    positives = [c for c in cases if c["review_category"] == "review_manually_annotated_chirp"]
    negatives = [c for c in cases if c["review_category"] == "review_candidate_on_no_chirp_wav"]
    require(len(positives) == 22 and len(negatives) == 13 and
            {(c["input_dataset_wav"], str(c["manual_chirp_id"])) for c in positives}
            == recovered, "P1 human reviewed positives are not the 22 P2 rescues")
    require(all(c["review_only_marker"] is True for c in negatives)
            and all(c["review_only_marker"] is False for c in positives),
            "P1 label cohort mismatch")
    manifest = csv_rows(dataset / "manifest.csv")
    lookup = {r["dataset_wav"]: r for r in manifest if r["partition"] == "calibration"}
    require(len(lookup) == 428, "frozen calibration membership changed")
    grouped = defaultdict(list)
    for case in cases:
        grouped[case["input_dataset_wav"]].append(case)
    results = []
    for index, (name, group) in enumerate(sorted(grouped.items()), 1):
        row = lookup.get(name)
        require(row is not None and len({c["review_category"] for c in group}) == 1,
                "unknown or mixed P1 review WAV")
        wav = checked_path(dataset, name, "calibration/wav/")
        annotation = checked_path(dataset, row["dataset_json"],
                                  "calibration/annotations/")
        require(sha256(wav).lower() == row["wav_sha256"].lower()
                and sha256(annotation).lower() == row["json_sha256"].lower(),
                "frozen case WAV/JSON bytes changed")
        doc = json.loads(annotation.read_text(encoding="utf-8-sig"))
        record = doc["files"][wav.name]
        record = remove_excluded_references({name: record})[0][name]
        manual = bench._manual_annotations(record)
        negative = group[0]["review_category"] == "review_candidate_on_no_chirp_wav"
        require((len(manual) == 0 and record.get("review_status") == "no_chirp")
                if negative else (len(manual) > 0 and record.get("review_status") != "no_chirp"),
                "positive/negative annotation integrity mismatch")
        y, sr = sf.read(wav, always_2d=False)
        if y.ndim == 2:
            y = np.mean(y, axis=1)
        baseline, hybrid, extras = hybrid_candidates(y, int(sr), detector)
        if negative:
            require(name in neg_rows and len(extras) == int(neg_rows[name]["added_candidates"]),
                    "P2 negative candidate counts differ")
        assignments = ({int(j): int(i) for i, j in
                        bench.match_candidates(hybrid, manual,
                                               geometry_mode="square_normals_v1")}
                       if not negative else {})
        used_extras = set()
        observed_negative_tracks = 0
        for case in group:
            n, candidate = find_extra(case, extras)
            require(n not in used_extras, "two review cases refer to the same extra")
            used_extras.add(n)
            if not negative:
                match = [j for j, manual_entry in enumerate(manual)
                         if str(manual_entry[0]) == str(case["manual_chirp_id"])]
                require(len(match) == 1 and match[0] in assignments and
                        identity(hybrid[assignments[match[0]]]) == identity(candidate),
                        "P1 reviewed recovered positive does not match P2 seed")
            curve = track(y, int(sr), candidate, bench,
                          {"n_fft": None, "hop": None, "search_margin_ms": None,
                           "trim_echo_rebound": True})
            features = curve_features(curve, candidate)
            tracked = features is not None
            if negative:
                observed_negative_tracks += int(tracked)
            else:
                require(tracked, "P2 rescued chirp no longer tracked")
            results.append({
                "case_id": case["case_id"], "wav": name,
                "label": "reviewed_artifact" if negative else "validated_chirp",
                "snr_group": ("none" if negative else
                              "high" if case["review_priority"] == 1 else "medium"),
                "chirp_id": "" if negative else str(case["manual_chirp_id"]),
                "tracked": tracked,
                "candidate_source": case["detector_variant"],
                "candidate_peak_time_ms": case["candidate_peak_time_ms"],
                "candidate_peak_frequency_khz": case["candidate_peak_freq_khz"],
                "features": features,
            })
        require(len(used_extras) == len(group), "review candidates omitted")
        if negative:
            require(observed_negative_tracks ==
                    int(neg_rows[name]["added_tracked_proposals"]),
                    "P2 negative tracked proposals cannot be reproduced")
        if index % 10 == 0 or index == len(grouped):
            print("P21_PROGRESS: reviewed_wavs=" + str(index) + "/" +
                  str(len(grouped)), flush=True)
    require(len(grouped) == 22 and len(results) == 35,
            "P1 review WAV count or case count differs")
    require(sum(r["tracked"] for r in results if r["label"] == "reviewed_artifact") == 7,
            "7 P2 extra negative tracks were not reproduced")
    require(sum(r["tracked"] for r in results if r["label"] == "validated_chirp") == 22,
            "22 P2 rescued positive tracks were not reproduced")
    gates = gate_table(results)
    positive = [r for r in results if r["label"] == "validated_chirp"]
    summary = {
        "schema_version": 1, "status": "complete",
        "scope": "P2.1 exploratory, pinned calibration, P1 human classification",
        "p0_detector_sha": DETECTOR_REFERENCE,
        "p0_lab_sha": LAB_REFERENCE, "p2_experiment_sha": P2_SHA,
        "dataset_manifest_sha256": MANIFEST_REFERENCE,
        "p1_decisions_sha256": p1["decisions_sha256"],
        "p2_negative_baseline_tracks": p2["baseline"]["negative_tracked_proposals"],
        "p2_negative_hybrid_tracks": p2["hybrid"]["negative_tracked_proposals"],
        "reviewed_wavs": len(grouped), "rescued_chirps": len(positive),
        "artifact_candidates": len(negatives),
        "artifact_candidates_tracked": sum(r["tracked"] for r in negatives_as_rows(results)),
        "artifact_candidates_rejected_by_tracker": sum(not r["tracked"] for r in negatives_as_rows(results)),
        "by_cohort": distribution(results),
        "fixed_diagnostic_gates": gates,
        "zero_loss_and_some_extra_artifacts_blocked_on_same_calibration": [
            key for key, value in gates.items()
            if value["rescued_chirps_rejected_of_22"] == 0
            and value["extra_negative_tracks_suppressed_of_7"] > 0
        ],
        "independent_validation_done": False,
        "final_production_false_alarms_measured": False,
        "heldout_test_wav_json_opened": False,
        "scientific_merge_authorized": False,
        "scientific_limitations": (
            "All 35 examples and morphological rules come from calibration and P1 "
            "human review. Gate evaluations on the same cases are exploratory and "
            "may overfit. These 7 are tracker-produced proposals in reviewed-no-chirp "
            "WAVs, not production false-positive counts. Annotations on positive WAVs "
            "are non-exhaustive. No optimization or threshold selection is performed."
        ),
    }
    return summary, results


def negatives_as_rows(rows):
    return [r for r in rows if r["label"] == "reviewed_artifact"]


def main():
    p = argparse.ArgumentParser()
    for arg in ("dataset", "review-project", "p1", "p2",
                "processing", "output"):
        p.add_argument("--" + arg, type=Path, required=True)
    args = p.parse_args()
    output = args.output.resolve()
    require(not output.exists() and not output.is_relative_to(args.dataset.resolve())
            and not output.is_relative_to(args.review_project.resolve())
            and not output.is_relative_to(args.p2.resolve()),
            "refuse to overwrite or write into scientific inputs")
    summary, rows = compute(args)
    output.mkdir(parents=True, exist_ok=False)
    fields = (
        "case_id", "wav", "label", "snr_group", "chirp_id", "tracked",
        "candidate_source", "candidate_peak_time_ms", "candidate_peak_frequency_khz",
        *METRICS,
    )
    with (output / "case_features.csv").open("w", newline="", encoding="utf-8") as f:
        w = csv.DictWriter(f, fieldnames=fields)
        w.writeheader()
        for row in rows:
            w.writerow({
                **{key: row.get(key, "") for key in fields},
                **(row["features"] or {}),
            })
    (output / "report.json").write_text(
        json.dumps(summary, indent=2, ensure_ascii=False, allow_nan=False) + "\n",
        encoding="utf-8")
    compact = {
        "status": summary["status"], "true_tracks": summary["rescued_chirps"],
        "false_extra_tracks": summary["artifact_candidates_tracked"],
        "false_extra_untracked": summary["artifact_candidates_rejected_by_tracker"],
        "zero_loss_exploratory_gates": summary[
            "zero_loss_and_some_extra_artifacts_blocked_on_same_calibration"],
        "scientific_merge_authorized": False,
    }
    print("P21_ARTIFACT_DIAGNOSTICS: " +
          json.dumps(compact, ensure_ascii=False, separators=(",", ":")),
          flush=True)


if __name__ == "__main__":
    main()
