"""P2 experimental end-to-end hybrid candidate -> full ridge tracking evaluation.

Pinned calibration only. No changes to the baseline detector, annotations,
review decisions, test cohort, or geometric scoring. No model is promoted.

Candidate rescue logic is the exact previously reviewed union:
- start with ALL original adaptive_v4 candidates;
- add only new NMS 6 ms candidates with frequency span >= 20 kHz;
- add only new width 1.0 ms candidates with peak frequency >= 100 kHz.

We distinguish unpaired candidate alarms on reviewed no_chirp WAVs from
candidates that the EXISTING reassignment tracker returns as full ridges.
The latter are tracked false proposals under the WAV-level no_chirp label,
not an independently calibrated final application-level false-alarm rate.
"""
from __future__ import annotations

import argparse
import csv
import hashlib
import json
import math
import subprocess
import sys
import tempfile
from collections import Counter, defaultdict
from pathlib import Path
from unittest.mock import patch

LAB_REFERENCE = "7ec740313af65a2c10f4926bc1a233c4aaa4ed70"
DETECTOR_REFERENCE = "db919efb8db0bca3692385383762b50e9cd3ae16"
MANIFEST_REFERENCE = "de326bd6f9aeb25d7792968db6ad46b5f0a7eeb8c7ae4dbad87e046810a03e31"
P1_EXPECTED = {"positive_high": 6, "positive_medium": 16, "negative": 13}
GROUPS = ("low_15_db_or_less", "medium_15_to_35_db", "high_35_db_or_more")


def require(ok, reason):
    if not ok:
        raise ValueError(reason)


def sha256(path):
    h = hashlib.sha256()
    with Path(path).open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            h.update(chunk)
    return h.hexdigest()


def revision(repo):
    sha = subprocess.check_output(
        ["git", "-C", str(repo), "rev-parse", "HEAD"], text=True).strip()
    dirty = subprocess.check_output(
        ["git", "-C", str(repo), "status", "--porcelain"], text=True).strip()
    require(len(sha) == 40 and not dirty, "pinned experiment checkout is dirty")
    return sha


def identity(c):
    """Frozen, exact identity used by the prior P1 candidate morphology screen."""
    return (round(float(c["time_mid"]), 11),
            round(float(c["peak_freq_hz"]), 4),
            round(float(c["t_start"]), 11),
            round(float(c["t_end"]), 11),
            round(float(c["f_low"]), 3),
            round(float(c["f_high"]), 3),
            str(c.get("detector_branch")))


def hybrid_candidates(y, sr, detector):
    """Use original extractor, original V4 filter + NMS, preserve all base seeds."""
    kw = dict(percentile_q=96.0, fmin=20000, fmax=150000, n_fft=512, hop=128)
    raw10 = detector._extract_candidates(y, sr, snr_threshold_db=10.0, **kw)
    raw9 = detector._extract_candidates(y, sr, snr_threshold_db=9.0, **kw)
    general = [
        {**c, "detector_branch": "general"} for c in raw10
        if int(c["size"]) > 10 and float(c["height_hz"]) > 5000.0
        and float(c["slope_hz_per_ms"]) <= -500.0
    ]
    low = [
        {**c, "detector_branch": "lowfreq"} for c in raw9
        if float(c["f_high"]) <= 45000.0
        and int(c["size"]) > 10 and float(c["height_hz"]) >= 2000.0
        and float(c["width_ms"]) >= 3.0
        and -1500.0 <= float(c["slope_hz_per_ms"]) < 0.0
    ]
    baseline = detector._time_freq_nms(
        [c for c in general if float(c["width_ms"]) >= 1.33] + low,
        20.0, 25000.0)
    nms6 = detector._time_freq_nms(
        [c for c in general if float(c["width_ms"]) >= 1.33] + low,
        6.0, 25000.0)
    width1 = detector._time_freq_nms(
        [c for c in general if float(c["width_ms"]) >= 1.0] + low,
        20.0, 25000.0)
    # The experiment is compared with the unmodified library, never a
    # substitute detector tailored to the manual reference trajectories.
    library = detector.detect_candidates_snr_blobs(
        y, sr, slope_filter_mode="adaptive_v4")
    require([identity(c) for c in baseline] == [identity(c) for c in library],
            "reconstructed baseline candidates differ from original detector")
    seen = {identity(c) for c in baseline}
    require(len(seen) == len(baseline), "baseline candidate identities are not unique")
    extras = []
    sources = ((nms6, lambda c: float(c["height_hz"]) >= 20000.0),
               (width1, lambda c: float(c["peak_freq_hz"]) >= 100000.0))
    for options, predicate in sources:
        for candidate in options:
            key = identity(candidate)
            if key not in seen and predicate(candidate):
                seen.add(key)
                extras.append(candidate)
    require(len(seen) == len(baseline) + len(extras),
            "hybrid candidate identities are duplicated")
    return baseline, [*baseline, *extras], extras


def validate_inputs(dataset, reference, p1, review_project, processing):
    require(revision(processing) == DETECTOR_REFERENCE,
            "detector Git commit differs from P0")
    require(sha256(dataset / "manifest.csv") == MANIFEST_REFERENCE,
            "frozen manifest differs from P0")
    m = reference["metadata"]
    f = m["frozen_dataset"]
    require(m["status"] == "complete" and f["partition"] == "calibration"
            and f["hashes_verified"] and f["manifest_sha256"] == MANIFEST_REFERENCE,
            "incomplete P0 calibration report")
    require(m["laboratory_git"]["git_commit"] == LAB_REFERENCE
            and m["algorithm_git"]["git_commit"] == DETECTOR_REFERENCE,
            "P0 scientific algorithm pins changed")
    require(m["scoring_protocol"] == "square_normals_v1"
            and m["geometric_tolerances_calibrated"] is False,
            "geometric scoring protocol drift")
    require(m["positive_annotations_exhaustive"] is False
            and m["include_reviewed_negatives"] is True,
            "annotation completeness or reviewed-negative protocol drift")
    require(Path(f["dataset"]).resolve() == dataset, "P0 dataset directory mismatch")
    options = m["options"]
    require(options["geometry_mode"] == "square_normals_v1"
            and options["n_fft"] is None and options["hop"] is None
            and options["v4_general_min_width_ms"] is None
            and options["search_margin_ms"] is None
            and options["trim_echo_rebound"] is True
            and options["low_snr_importance_weight"] == 0.2,
            "P0 benchmark options changed")
    require(p1["status"] == "passed" and p1["review_complete"] is True
            and p1["reviewed"] == 35 and p1["pending"] == 0
            and p1["manifest_sha256"] == MANIFEST_REFERENCE,
            "P1 scientific human decisions not fully validated")
    require(p1["test_wav_json_opened"] is False and
            p1["human_decisions_modified"] is False,
            "P1 provenance violates read-only / heldout constraint")
    require(sha256(review_project / "human_decisions.csv") == p1["decisions_sha256"],
            "P1 human decisions have changed; fresh P1 audit required")
    for group, expected in P1_EXPECTED.items():
        entry = p1["cohorts"][group]
        require(entry["total"] == expected and entry["reviewed"] == expected
                and entry["uncertain"] == 0, "P1 review cohort changed")
        if group == "negative":
            require(entry["negative_true_chirp_label_conflict"] == 0
                    and entry["negative_artifact_or_echo"] == 13,
                    "P1 negative artifact classifications inconclusive")
        else:
            require(entry["positive_confirmed"] == expected,
                    "P1 positive chirp classifications inconclusive")
    return options


def check_baseline_matches(name, prior_rows, reference_details, baseline, manual, bench):
    matches = bench.match_candidates(baseline, manual,
                                     geometry_mode="square_normals_v1")
    seen = {j: i for i, j in matches}
    require(len(matches) == prior_rows["matched"], "P0 match count differs")
    require(len(manual) == prior_rows["annotated"], "P0 manual reference count differs")
    require(len(reference_details) == len(manual), "P0 details missing")
    for j, d in enumerate(reference_details):
        require(str(manual[j][0]) == str(d["chirp_id"]), "manual chirp identity changed")
        require(bool(d["detector_matched"]) == (j in seen), "baseline match identity differs")
        if j in seen:
            require(int(d["candidate_index"]) == seen[j],
                    "baseline candidate matching index changed")
    return seen


def track_negative(y, sr, candidate, bench, opts):
    diagnostics = {}
    try:
        params = ({"search_margin_ms": opts["search_margin_ms"]}
                  if opts["search_margin_ms"] is not None else {})
        curve = bench.track_reassigned_candidate(
            y, sr, candidate, n_fft=opts["n_fft"], hop=opts["hop"],
            use_broad_seed=True, complete_ends=True,
            trim_echo_rebound=opts["trim_echo_rebound"],
            diagnostics=diagnostics, **params)
        return {"tracked": curve is not None,
                "points": int(len(curve)) if curve is not None else 0,
                "error": None}
    except Exception as e:
        # P0 matches only manual positives; negative tracking is newly measured
        # and must expose any failures instead of silently ignoring them.
        return {"tracked": False, "points": 0,
                "error": type(e).__name__}


def compute(args):
    import numpy as np
    import soundfile as sf
    from laboratory.frozen_dataset import collect_frozen_dataset
    from laboratory.runner import remove_excluded_references
    from laboratory.dataset import stable_hash
    import bat_analysis.detection as detector
    from benchmark import reassignment_detector_seeds as bench

    dataset = args.dataset.resolve()
    processing = args.processing.resolve()
    assert Path(detector.__file__).resolve().is_relative_to(processing)
    assert Path(bench.__file__).resolve().is_relative_to(processing)
    reference = json.loads(args.reference.read_text(encoding="utf-8-sig"))
    p1 = json.loads(args.p1.read_text(encoding="utf-8-sig"))
    opts = validate_inputs(dataset, reference, p1, args.review_project.resolve(),
                           processing)
    previous = {r["file"]: r for r in reference["per_file"]}
    require(len(previous) == 240 and reference["totals"]["files_missing"] == 0
            and reference["totals"]["annotated"] == 493
            and reference["totals"]["matched"] == 401
            and reference["totals"]["reviewed_no_chirp"]["wav_count"] == 203,
            "unexpected P0 reference cohort")
    details_by_file = defaultdict(list)
    for detail in reference["details"]:
        details_by_file[detail["file"]].append(detail)
    records, provenance = collect_frozen_dataset(dataset)
    require(provenance["partition"] == "calibration" and
            provenance["wav_count"] == 428 and provenance["hashes_verified"],
            "frozen calibration inputs not validated")
    selected, excluded = remove_excluded_references(records)
    selected = {k: selected[k] for k in previous}
    require(len(selected) == 240, "P0 selected cohort incomplete")
    pos_details = []
    negatives = []
    file_rows = []
    positive_files = 0
    negative_files = 0
    additional_positive = 0
    additional_negative = 0
    baseline_negative_count = 0
    negative_wav_alerts = {"baseline": 0, "hybrid": 0}
    negative_tracked_wavs = {"baseline": 0, "hybrid": 0}
    negative_tracked_proposals = {"baseline": 0, "hybrid": 0}
    negative_tracker_exceptions = Counter()
    negative_seconds = 0.0
    switched_existing = 0
    for index, (name, record) in enumerate(selected.items(), 1):
        prior = previous[name]
        require(stable_hash(record) ==
                reference["metadata"]["input_fingerprints"][name]["annotation_sha256"],
                "P0 filtered annotation fingerprint drift")
        negative = prior.get("reference_class") == "reviewed_no_chirp"
        manual = bench._manual_annotations(record)
        require(bool(manual) != negative, "positive / negative annotation cohort ambiguity")
        wav = (dataset / name).resolve()
        require(wav.is_relative_to(dataset) and name.startswith("calibration/wav/"),
                "selected WAV outside calibration")
        y, sr = sf.read(wav, always_2d=False)
        if y.ndim == 2:
            y = np.mean(y, axis=1)
        baseline, hybrid, extras = hybrid_candidates(y, int(sr), detector)
        require(len(baseline) == int(prior["candidates"]),
                "P0 candidate count changed")
        require(len(hybrid) == len(baseline) + len(extras),
                "hybrid did not preserve baseline candidate set")
        if negative:
            negative_files += 1
            require(record.get("review_status") == "no_chirp" and not manual,
                    "false reviewed-negative reference")
            require(not details_by_file[name], "unexpected negative manual references")
            additional_negative += len(extras)
            baseline_negative_count += len(baseline)
            duration = float(sf.info(wav).duration)
            negative_seconds += duration
            tracking = [track_negative(y, int(sr), c, bench, opts) for c in hybrid]
            for d in tracking:
                if d["error"]:
                    negative_tracker_exceptions[d["error"]] += 1
            old_tracks = sum(bool(d["tracked"]) for d in tracking[:len(baseline)])
            extra_tracks = sum(bool(d["tracked"]) for d in tracking[len(baseline):])
            negative_tracked_proposals["baseline"] += old_tracks
            negative_tracked_proposals["hybrid"] += old_tracks + extra_tracks
            negative_tracked_wavs["baseline"] += int(old_tracks > 0)
            negative_tracked_wavs["hybrid"] += int(old_tracks + extra_tracks > 0)
            negative_wav_alerts["baseline"] += int(bool(baseline))
            negative_wav_alerts["hybrid"] += int(bool(hybrid))
            negatives.append({
                "wav": name, "baseline_candidates": len(baseline),
                "added_candidates": len(extras),
                "baseline_tracked_proposals": old_tracks,
                "added_tracked_proposals": extra_tracks,
                "baseline_wav_has_tracked_proposal": bool(old_tracks),
                "hybrid_wav_has_tracked_proposal": old_tracks + extra_tracks > 0,
                "tracking_exceptions": sum(bool(d["error"]) for d in tracking),
                "duration_seconds": duration,
            })
        else:
            positive_files += 1
            require(record.get("review_status") != "no_chirp",
                    "positive record mislabelled no_chirp")
            base_match = check_baseline_matches(
                name, prior, details_by_file[name], baseline, manual, bench)
            # Patch ONLY the evaluation call in this private experiment process,
            # not the on-disk detector, so scoring and tracker remain unchanged.
            with tempfile.TemporaryDirectory(prefix="p2-chirp-") as temp:
                annotation = Path(temp) / "single.json"
                annotation.write_text(json.dumps({"files": {name: record}},
                                                 ensure_ascii=False), encoding="utf-8")
                def candidate_supply(samples, frequency, *, slope_filter_mode,
                                     **detector_kwargs):
                    require(slope_filter_mode == "adaptive_v4"
                            and detector_kwargs == {},
                            "unexpected detector parameters from benchmark")
                    require(int(frequency) == int(sr) and len(samples) == len(y),
                            "benchmark audio signal changed")
                    return hybrid
                with patch.object(bench, "detect_candidates_snr_blobs", candidate_supply):
                    candidate_report = bench.evaluate(
                        annotation, dataset, n_fft=opts["n_fft"], hop=opts["hop"],
                        max_per_file=None, v4_general_min_width_ms=None,
                        low_snr_importance_weight=opts["low_snr_importance_weight"],
                        search_margin_ms=opts["search_margin_ms"],
                        trim_echo_rebound=opts["trim_echo_rebound"],
                        geometry_mode=opts["geometry_mode"])
            outcome = candidate_report["per_file"]
            require(len(outcome) == 1 and "error" not in outcome[0],
                    "hybrid benchmark WAV failed")
            require(outcome[0]["annotated"] == len(manual)
                    and outcome[0]["candidates"] == len(hybrid),
                    "hybrid benchmark silently skipped references")
            rows = candidate_report["details"]
            require(len(rows) == len(manual), "hybrid reference details incomplete")
            hybrid_match = {j: int(d["candidate_index"]) for j, d in enumerate(rows)
                            if d["detector_matched"]}
            additional_positive += len(extras)
            switched_existing += sum(
                identity(hybrid[hybrid_match[j]]) != identity(baseline[base_match[j]])
                for j in set(hybrid_match) & set(base_match))
            for j, detail in enumerate(rows):
                before = details_by_file[name][j]
                require(str(detail["chirp_id"]) == str(before["chirp_id"])
                        and math.isclose(float(detail["snr_max_db"]), float(before["snr_max_db"]),
                                         rel_tol=1e-7, abs_tol=1e-5),
                        "baseline reference SNR or identity changed")
                pos_details.append(detail)
        if index % 40 == 0 or index == 240:
            print("P2_PROGRESS: processed=" + str(index) + "/240", flush=True)

    require(positive_files == 37 and negative_files == 203,
            "P0 calibration WAV cohorts drifted")
    require(len(pos_details) == 493, "full positive reference cohort missing")
    require(sum(bool(x["detector_matched"]) for x in pos_details) == 423,
            "hybrid candidate recovery differs from pinned P1 hypothesis")
    require(additional_negative == 13, "extra reviewed-negative candidate count drifted")
    require(baseline_negative_count == 677, "P0 negative candidate count drifted")
    require(negative_wav_alerts == {"baseline": 108, "hybrid": 108},
            "reviewed-negative WAV alert count drifted")
    previous_details = reference["details"]
    require(len(previous_details) == 493, "P0 detailed scores missing")
    old_map = {(r["file"], str(r["chirp_id"])): r for r in previous_details}
    new_map = {(r["file"], str(r["chirp_id"])): r for r in pos_details}
    require(len(old_map) == len(new_map) == 493 and old_map.keys() == new_map.keys(),
            "hybrid source reference identities changed")
    weighted = bench.summarize_importance(pos_details)
    by_snr = bench.summarize_by_snr(pos_details)
    old_weighted = reference["totals"]["weighted_full_pipeline_within_tolerance_fraction"]
    new_weighted = weighted["weighted_full_pipeline_within_tolerance_fraction"]
    base_within = reference["totals"]["full_pipeline_within_tolerance_fraction"]
    new_within = sum(float(r.get("within_snr_tolerance_fraction") or 0.0)
                     for r in pos_details) / 493
    baseline_ids = {key for key, r in old_map.items() if r["detector_matched"]}
    hybrid_ids = {key for key, r in new_map.items() if r["detector_matched"]}
    recovered = hybrid_ids - baseline_ids
    lost = baseline_ids - hybrid_ids
    improvement = []
    for key in old_map:
        a, b = old_map[key], new_map[key]
        improvement.append({
            "file": key[0], "chirp_id": key[1],
            "snr_db": b.get("snr_max_db"),
            "baseline_matched": bool(a["detector_matched"]),
            "hybrid_matched": bool(b["detector_matched"]),
            "baseline_tracked": bool(a["tracking_success"]),
            "hybrid_tracked": bool(b["tracking_success"]),
            "baseline_tolerance_fraction": a.get("within_snr_tolerance_fraction"),
            "hybrid_tolerance_fraction": b.get("within_snr_tolerance_fraction"),
            "baseline_mean_error_khz": a.get("mean_full_interval_error_khz"),
            "hybrid_mean_error_khz": b.get("mean_full_interval_error_khz"),
            "baseline_coverage": a.get("coverage_fraction"),
            "hybrid_coverage": b.get("coverage_fraction"),
            "baseline_start_error_ms": a.get("start_error_ms"),
            "hybrid_start_error_ms": b.get("start_error_ms"),
            "baseline_end_error_ms": a.get("end_error_ms"),
            "hybrid_end_error_ms": b.get("end_error_ms"),
        })
    # Track success on negatives is a property of the ridge tracker, not an
    # independently calibrated biological or production final false positive.
    errors = sum(negative_tracker_exceptions.values())
    full_status = "complete" if errors == 0 else "incomplete"
    summary = {
        "schema_version": 1, "status": full_status,
        "scientific_scope": "exploratory calibration-only / no deployment",
        "hypothesis": "baseline union NMS6-height20k plus width1-peak100k",
        "dataset_manifest_sha256": MANIFEST_REFERENCE,
        "p0_lab_sha": LAB_REFERENCE, "p0_detector_sha": DETECTOR_REFERENCE,
        "p2_experiment_sha": revision(Path(__file__).resolve().parents[1]),
        "p1_human_decisions_sha256": p1["decisions_sha256"],
        "positive_annotations_exhaustive": False,
        "geometric_tolerances_calibrated": False,
        "calibration_wavs": 428, "positive_wavs": positive_files,
        "reviewed_negative_wavs": negative_files, "manual_references": 493,
        "baseline": {
            "matched": reference["totals"]["matched"],
            "tracked": reference["totals"]["tracked"],
            "weighted_detector_recall": reference["totals"]["weighted_detector_recall"],
            "weighted_full_pipeline_within_tolerance_fraction": old_weighted,
            "full_pipeline_within_tolerance_fraction": base_within,
            "negative_candidates": baseline_negative_count,
            "negative_wavs_with_candidates": negative_wav_alerts["baseline"],
            "negative_tracked_proposals": negative_tracked_proposals["baseline"],
            "negative_wavs_with_tracked_proposals": negative_tracked_wavs["baseline"],
        },
        "hybrid": {
            "matched": sum(bool(r["detector_matched"]) for r in pos_details),
            "tracked": sum(bool(r["tracking_success"]) for r in pos_details),
            **weighted,
            "full_pipeline_within_tolerance_fraction": new_within,
            "by_snr": by_snr,
            "negative_candidates": baseline_negative_count + additional_negative,
            "negative_wavs_with_candidates": negative_wav_alerts["hybrid"],
            "negative_tracked_proposals": negative_tracked_proposals["hybrid"],
            "negative_wavs_with_tracked_proposals": negative_tracked_wavs["hybrid"],
        },
        "differences": {
            "recovered_chirps": len(recovered), "lost_chirps": len(lost),
            "existing_matched_chirp_switched_candidate": switched_existing,
            "added_positive_candidates": additional_positive,
            "added_negative_candidates": additional_negative,
            "added_negative_tracked_proposals": (
                negative_tracked_proposals["hybrid"] -
                negative_tracked_proposals["baseline"]),
            "weighted_full_pipeline_score_absolute_change": new_weighted - old_weighted,
            "unweighted_full_pipeline_score_absolute_change": new_within - base_within,
        },
        "negative_wav_total_duration_s": negative_seconds,
        "negative_track_errors_by_type": dict(negative_tracker_exceptions),
        "negative_candidate_tracking_scope":
            "all baseline and added detector candidates on reviewed no_chirp WAVs; "
            "no application post-filter, morphology label or independent adjudication",
        "gate": {
            "reference_preserved": len(lost) == 0,
            "weighted_full_pipeline_nonregression": new_weighted >= old_weighted - 1e-9,
            "reviewed_negative_tracked_proposals_nonregression": (
                negative_tracked_proposals["hybrid"] <= negative_tracked_proposals["baseline"]),
            "reviewed_negative_tracked_wavs_nonregression": (
                negative_tracked_wavs["hybrid"] <= negative_tracked_wavs["baseline"]),
            "negative_candidate_strict_nonregression": additional_negative == 0,
            "error_free": errors == 0,
            "candidate_only_human_review": "35/35 classified on calibration, not held-out performance",
            "authorized_for_scientific_merge": False,
        },
        "methodological_limitations": (
            "Morphology rules selected on calibration; 35 human reviews not an independent "
            "test. Positive WAV labels non-exhaustive, never score other candidates as false "
            "chirps there. The test partition is never loaded. Negative curves are tracker "
            "outputs, not complete production detections. Endpoints require independent "
            "geometric and human validation. No auto-merges."
        ),
    }
    return summary, improvement, negatives


def main():
    parser = argparse.ArgumentParser()
    for key in ("dataset", "reference", "p1", "review-project",
                "processing", "output"):
        parser.add_argument("--" + key, type=Path, required=True)
    args = parser.parse_args()
    root = args.output.resolve()
    require(not root.exists() and not root.is_relative_to(args.dataset.resolve())
            and not root.is_relative_to(args.review_project.resolve()),
            "output must be new, outside frozen dataset and native human review")
    try:
        summary, cases, negatives = compute(args)
        root.mkdir(parents=True, exist_ok=False)
        with (root / "positive_trajectory_pairs.csv").open("w", newline="", encoding="utf-8") as f:
            writer = csv.DictWriter(f, fieldnames=list(cases[0]))
            writer.writeheader()
            writer.writerows(cases)
        with (root / "negative_tracked_proposals.csv").open("w", newline="", encoding="utf-8") as f:
            writer = csv.DictWriter(f, fieldnames=list(negatives[0]))
            writer.writeheader()
            writer.writerows(negatives)
        (root / "report.json").write_text(
            json.dumps(summary, indent=2, ensure_ascii=False, allow_nan=False) + "\n",
            encoding="utf-8")
        short = {
            "status": summary["status"],
            "positive_wavs": summary["positive_wavs"],
            "negative_wavs": summary["reviewed_negative_wavs"],
            "baseline_matched": summary["baseline"]["matched"],
            "hybrid_matched": summary["hybrid"]["matched"],
            "baseline_tracked": summary["baseline"]["tracked"],
            "hybrid_tracked": summary["hybrid"]["tracked"],
            "weighted_full_pipeline_change": summary["differences"]["weighted_full_pipeline_score_absolute_change"],
            "negative_tracked_baseline": summary["baseline"]["negative_tracked_proposals"],
            "negative_tracked_hybrid": summary["hybrid"]["negative_tracked_proposals"],
            "negative_tracking_errors": sum(summary["negative_track_errors_by_type"].values()),
            "scientific_merge_authorized": False,
        }
        print("P2_END_TO_END_RESULT: " + json.dumps(short, separators=(",", ":")),
              flush=True)
        return 0 if summary["status"] == "complete" else 2
    except Exception as exc:
        print("P2_END_TO_END_ERROR: " + type(exc).__name__ +
              " (no scientific conclusions; see runner-local diagnostics)", flush=True)
        raise


if __name__ == "__main__":
    sys.exit(main())
