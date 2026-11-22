"""P3.1 independent evaluator — executable only after immutable freeze.

One implementation handles CALIBRATION REPLAY and independent TEST, with exactly
the same fixed V4 / sweep8 / sweep8+contrast6 candidates. Test mode REQUIRES
a frozen pre-registration certificate. Positive files are NOT exhaustive
negative labels. Never modifies WAV, annotations, manual decisions or detector.
"""
from __future__ import annotations

import argparse
import csv
import hashlib
import json
import math
import random
import subprocess
import tempfile
from collections import Counter, defaultdict
from pathlib import Path
from unittest.mock import patch

from experiments.p2_hybrid_trajectory import (
    DETECTOR_REFERENCE, MANIFEST_REFERENCE, hybrid_candidates,
    identity, require, revision, sha256, track_negative,
)
from experiments.p24_full_calibration_gates import (
    analyze_extra, passes,
)

P24_PARENT_SHA = "b26236c0d431c7c287e689c09ce95b6b2df83999"
PRIMARY = "sweep8_and_normal_contrast8_ge6"
SECONDARY = "sweep_ge_8khz"
SEED = 42
BOOTSTRAP_REPLICATES = 2000


def validate_experiment_checkout(root: Path, expected_sha: str) -> None:
    require(revision(root) == expected_sha, "P3 experimental evaluator SHA/worktree drift")
    ancestor = subprocess.run(
        ["git", "-C", str(root), "merge-base", "--is-ancestor", P24_PARENT_SHA, expected_sha],
        check=False,
    )
    require(ancestor.returncode == 0, "P3 code does not inherit pinned P2.4 science")
    changed = subprocess.check_output(
        ["git", "-C", str(root), "diff", "--name-only", P24_PARENT_SHA, expected_sha],
        text=True,
    ).splitlines()
    require(set(changed) <= {
        "experiments/p31_independent_eval.py",
        "tests/test_p31_independent_eval.py",
    }, "P3 experimental code unexpectedly changes scientific dependencies")


def safe_relative(path: str, prefix: str) -> str:
    require(isinstance(path, str) and path.startswith(prefix) and
            "\\" not in path and ":" not in path and
            all(p not in ("", ".", "..") for p in path.split("/")),
            "invalid partition-relative input path")
    return path


def manifest_records(dataset: Path, partition: str) -> list[dict]:
    manifest = dataset / "manifest.csv"
    require(sha256(manifest) == MANIFEST_REFERENCE, "frozen manifest SHA changed")
    with manifest.open(newline="", encoding="utf-8-sig") as source:
        reader = csv.DictReader(source)
        require(reader.fieldnames is not None and
                len(reader.fieldnames) == len(set(reader.fieldnames)),
                "ambiguous manifest schema")
        # In test mode this is the first and only authorized read of test
        # membership. The frozen certificate is validated by the caller FIRST.
        rows = [r for r in reader if r.get("partition") == partition]
    names = [safe_relative(r["dataset_wav"], partition + "/wav/") for r in rows]
    require(len(names) == len(set(names)), "duplicate WAV in frozen partition")
    for r in rows:
        safe_relative(r["dataset_json"], partition + "/annotations/")
        require(len(r["wav_sha256"]) == len(r["json_sha256"]) == 64,
                "missing input hash in frozen manifest")
    require(rows, "requested partition has no eligible WAVs")
    return sorted(rows, key=lambda x: x["dataset_wav"])


def checked_file(dataset: Path, relative: str, digest: str) -> Path:
    path = (dataset / relative).resolve()
    require(path.is_relative_to(dataset) and path.is_file() and
            sha256(path).lower() == digest.lower(),
            "frozen WAV/annotation bytes changed")
    return path


def validate_freeze(proof: dict, expected_sha: str, request_nonce: str) -> None:
    """Fail closed before even reading a test partition manifest."""
    require(proof.get("schema_version") == 1 and proof.get("status") == "FROZEN" and
            proof.get("p31_test_execution_approved") is True and
            proof.get("scientific_auto_merge_authorized") is False and
            proof.get("manifest_sha256") == MANIFEST_REFERENCE and
            proof.get("evaluator_sha") == expected_sha and
            proof.get("one_shot_request_nonce") == request_nonce and
            len(str(proof.get("p30c_report_sha256", ""))) == 64,
            "missing or mismatched P3 preregistration freeze certificate")


def evaluate_positive(dataset, name, record, baseline, hybrid, extras, y, sr,
                      bench, detector):
    """Use pinned benchmark unmodified, only mock candidate supply per P2."""
    import numpy as np
    from benchmark.snr_metrics import ReferenceSNR
    manual = bench._manual_annotations(record)
    require(bool(manual), "positive cohort has no validated manual chirp")
    original = detector.detect_candidates_snr_blobs(
        y, sr, slope_filter_mode="adaptive_v4",
    )
    require([identity(c) for c in baseline] == [identity(c) for c in original],
            "baseline candidates differ from V4 detector")
    require(len(hybrid) == len(baseline) + len(extras), "candidate union drift")

    def run(candidates):
        with tempfile.TemporaryDirectory(prefix="p31-calibration-safe-") as temp:
            annotation = Path(temp) / "single.json"
            annotation.write_text(json.dumps({"files": {name: record}},
                                             ensure_ascii=False), encoding="utf-8")
            def supply(samples, frequency, *, slope_filter_mode, **kwargs):
                require(slope_filter_mode == "adaptive_v4" and not kwargs and
                        int(frequency) == int(sr) and len(samples) == len(y),
                        "benchmark unexpectedly changed candidate extractor inputs")
                return candidates
            with patch.object(bench, "detect_candidates_snr_blobs", supply):
                return bench.evaluate(
                    annotation, dataset, n_fft=None, hop=None, max_per_file=None,
                    v4_general_min_width_ms=None, low_snr_importance_weight=.2,
                    search_margin_ms=None, trim_echo_rebound=True,
                    geometry_mode="square_normals_v1",
                )

    a = run(baseline)
    b = run(hybrid)
    require(len(a["per_file"]) == len(b["per_file"]) == 1 and
            "error" not in a["per_file"][0] and "error" not in b["per_file"][0],
            "pinned benchmark failed a positive WAV")
    old, new = a["details"], b["details"]
    require(len(old) == len(new) == len(manual), "benchmark dropped manual references")
    reference = None
    rows = []
    for i, (left, right) in enumerate(zip(old, new)):
        require(str(left["chirp_id"]) == str(right["chirp_id"]) ==
                str(manual[i][0]), "paired human annotation identity changed")
        require(math.isclose(float(left["snr_max_db"]),
                             float(right["snr_max_db"]), abs_tol=1e-5, rel_tol=1e-7),
                "SNR changed between paired V4 and hybrid")
        is_old = bool(left["detector_matched"] and left["tracking_success"])
        is_new = bool(right["detector_matched"] and right["tracking_success"])
        kept = {PRIMARY: is_new, SECONDARY: is_new}
        if is_new:
            index = int(right["candidate_index"])
            require(0 <= index < len(hybrid), "hybrid assignment index out of range")
            if index >= len(baseline):
                if reference is None:
                    reference = ReferenceSNR.from_audio(y, int(sr))
                extra = analyze_extra(y, int(sr), hybrid[index], bench, reference)
                require(extra["tracked"], "matched extra lost tracking in fixed replay")
                for label in kept:
                    kept[label] = passes(label, extra)
        rows.append({
            "file": name, "chirp_id": str(manual[i][0]),
            "snr_db": float(right["snr_max_db"]),
            "v4": is_old, "hybrid": is_new,
            "primary": bool(kept[PRIMARY]), "secondary": bool(kept[SECONDARY]),
            "v4_error_khz_eq": left.get("mean_full_interval_error_khz"),
            "hybrid_error_khz_eq": right.get("mean_full_interval_error_khz"),
            "primary_geometry_available": bool(kept[PRIMARY]),
        })
    return rows


def evaluate_negative(name, baseline, extras, y, sr, bench):
    from benchmark.snr_metrics import ReferenceSNR

    opts = {"n_fft": None, "hop": None,
            "search_margin_ms": None, "trim_echo_rebound": True}
    previous = [track_negative(y, sr, c, bench, opts) for c in baseline]
    require(all(not t["error"] for t in previous), "V4 negative tracker errors")
    base_tracks = sum(bool(t["tracked"]) for t in previous)
    more = {PRIMARY: 0, SECONDARY: 0}
    if extras:
        reference = ReferenceSNR.from_audio(y, sr)
        for candidate in extras:
            result = analyze_extra(y, sr, candidate, bench, reference)
            for gate in more:
                more[gate] += int(passes(gate, result))
    return {
        "file": name, "negative_duration_seconds": len(y) / float(sr),
        "v4_tracks": base_tracks,
        "primary_tracks": base_tracks + more[PRIMARY],
        "secondary_tracks": base_tracks + more[SECONDARY],
    }


def quantile(values, fraction):
    ordered = sorted(float(v) for v in values if math.isfinite(float(v)))
    require(ordered and 0 <= fraction <= 1, "empty or invalid percentile input")
    position = (len(ordered) - 1) * fraction
    low = int(position)
    high = min(low + 1, len(ordered) - 1)
    return ordered[low] + (ordered[high] - ordered[low]) * (position - low)


def paired_statistics(positives: list[dict], negatives: list[dict],
                      *, seed: int = SEED, replicates: int = BOOTSTRAP_REPLICATES) -> dict:
    """No post-hoc choice: one primary, one descriptive secondary."""
    require(positives, "no manually annotated test references")
    require(len({(r["file"], r["chirp_id"]) for r in positives}) == len(positives),
            "manual reference counted twice")
    require(all(r["v4"] is not None and r["primary"] is not None for r in positives),
            "incomplete paired reference results")
    refs = len(positives)
    reference = sum(int(r["v4"]) for r in positives)
    primary = sum(int(r["primary"]) for r in positives)
    secondary = sum(int(r["secondary"]) for r in positives)
    baseline_negative = sum(r["v4_tracks"] for r in negatives)
    primary_negative = sum(r["primary_tracks"] for r in negatives)
    secondary_negative = sum(r["secondary_tracks"] for r in negatives)
    hours = sum(r["negative_duration_seconds"] for r in negatives) / 3600.0
    groups = defaultdict(lambda: {"positives": [], "negatives": []})
    for row in positives:
        groups[row["group"]]["positives"].append(row)
    for row in negatives:
        groups[row["group"]]["negatives"].append(row)
    ci = None
    if len(groups) >= 3 and negatives and hours > 0:
        rng = random.Random(seed)
        keys = sorted(groups)
        deltas = []
        for _ in range(replicates):
            selection = rng.choices(keys, k=len(keys))
            pos = [r for key in selection for r in groups[key]["positives"]]
            if not pos:
                continue
            deltas.append(
                sum(int(r["primary"]) - int(r["v4"]) for r in pos) / len(pos))
        if len(deltas) >= int(replicates * .95):
            ci = {"p025": quantile(deltas, .025),
                  "p975": quantile(deltas, .975),
                  "replicates": len(deltas), "seed": seed,
                  "unit": "recording_session"}
    nonregression = bool(negatives) and hours > 0 and (
        primary_negative <= baseline_negative)
    superiority = ci is not None and ci["p025"] > 0.0
    return {
        "manual_references": refs,
        "reviewed_negative_wavs": len(negatives),
        "recording_groups": len(groups),
        "v4_tracked_manual_references": reference,
        "primary_tracked_manual_references": primary,
        "secondary_tracked_manual_references": secondary,
        "primary_paired_recall_delta": (primary - reference) / refs,
        "v4_negative_tracked_proposals": baseline_negative,
        "primary_negative_tracked_proposals": primary_negative,
        "secondary_negative_tracked_proposals": secondary_negative,
        "negative_hours": hours,
        "v4_negative_proposals_per_hour": baseline_negative / hours if hours else None,
        "primary_negative_proposals_per_hour": primary_negative / hours if hours else None,
        "primary_recall_cluster_ci95": ci,
        "negative_nonregression": nonregression,
        "primary_superiority_supported": bool(superiority),
        "promotion_candidate_eligible_for_human_review": bool(
            superiority and nonregression and len(groups) >= 3),
        "scientific_auto_merge_authorized": False,
        "limitations": [
            *([] if negatives else ["No explicitly reviewed no_chirp WAV: negative constraint unassessable"]),
            *([] if len(groups) >= 3 else ["Fewer than 3 recording sessions: no valid session-bootstrap CI"]),
            "Positive WAV annotations are incomplete; unmatched candidates there are never negatives",
            "Tracked no_chirp proposals are not final application-level false-alarm rates",
        ],
    }


def compute(args):
    import numpy as np
    import soundfile as sf
    import bat_analysis.detection as detector
    from benchmark import reassignment_detector_seeds as bench
    from laboratory.runner import remove_excluded_references

    dataset, processing = args.dataset.resolve(), args.processing.resolve()
    root = Path(__file__).resolve().parents[1]
    validate_experiment_checkout(root, args.expected_evaluator_sha)
    require(revision(processing) == DETECTOR_REFERENCE and
            Path(detector.__file__).resolve().is_relative_to(processing) and
            Path(bench.__file__).resolve().is_relative_to(processing),
            "unpinned V4 detector / benchmark imported")
    if args.partition == "test":
        require(args.freeze_proof is not None and args.request_nonce,
                "independent test locked; freeze proof and one-shot nonce required")
        proof = json.loads(args.freeze_proof.read_text(encoding="utf-8"))
        validate_freeze(proof, args.expected_evaluator_sha, args.request_nonce)
    else:
        require(args.freeze_proof is None and args.request_nonce is None,
                "calibration replay must not carry any held-out authorization")

    rows = manifest_records(dataset, args.partition)
    positives, negatives = [], []
    skipped_unlabelled = 0
    for index, row in enumerate(rows, 1):
        name = row["dataset_wav"]
        wav = checked_file(dataset, name, row["wav_sha256"])
        ann = checked_file(dataset, row["dataset_json"], row["json_sha256"])
        doc = json.loads(ann.read_text(encoding="utf-8-sig"))
        require(isinstance(doc.get("files"), dict) and wav.name in doc["files"],
                "annotation bundle does not reference expected WAV")
        record = remove_excluded_references({name: doc["files"][wav.name]})[0][name]
        manual = bench._manual_annotations(record)
        label_negative = record.get("review_status") == "no_chirp"
        require(not (manual and label_negative),
                "no_chirp record contradicts a validated human chirp")
        if not manual and not label_negative:
            skipped_unlabelled += 1
            continue
        y, sr = sf.read(wav, always_2d=False)
        if y.ndim == 2:
            y = np.mean(y, axis=1)
        require(y.ndim == 1 and len(y) and sr > 0,
                "unsupported audio data or sample rate")
        base, hybrid, extras = hybrid_candidates(y, int(sr), detector)
        group = str(row.get("session") or row["dataset_json"])
        if label_negative:
            item = evaluate_negative(name, base, extras, y, int(sr), bench)
            item["group"] = group
            negatives.append(item)
        else:
            details = evaluate_positive(
                dataset, name, record, base, hybrid, extras, y, int(sr),
                bench, detector)
            for item in details:
                item["group"] = group
            positives.extend(details)
        if index % 25 == 0:
            print(f"P31_REPLAY_PROGRESS: partition={args.partition} processed={index}/{len(rows)}",
                  flush=True)
    stats = paired_statistics(positives, negatives)
    if args.partition == "calibration":
        require(stats["manual_references"] == 493 and
                stats["reviewed_negative_wavs"] == 203 and
                stats["v4_tracked_manual_references"] == 401 and
                stats["primary_tracked_manual_references"] == 421 and
                stats["secondary_tracked_manual_references"] == 423 and
                stats["v4_negative_tracked_proposals"] == 524 and
                stats["primary_negative_tracked_proposals"] == 524 and
                stats["secondary_negative_tracked_proposals"] == 528,
                "P3 evaluator cannot reproduce P2.4 complete calibration counts")
    return {
        "schema_version": 1,
        "status": "complete",
        "partition": args.partition,
        "independent_validation": args.partition == "test",
        "dataset_manifest_sha256": MANIFEST_REFERENCE,
        "experiment_sha": args.expected_evaluator_sha,
        "detector_sha": DETECTOR_REFERENCE,
        "p24_inherited_sha": P24_PARENT_SHA,
        "skipped_unlabelled_wavs": skipped_unlabelled,
        "eligible_positive_wavs": len(set(x["file"] for x in positives)),
        "statistics": stats,
        "scoring": {
            "candidate_matching": "pinned square_normals_v1, Hungarian one-to-one",
            "time_slack_ms": 1.5,
            "max_seed_error_khz_eq": 18.0,
            "primary": PRIMARY, "secondary": SECONDARY,
            "tracking": "pinned benchmark, n_fft/hop/search_margin=None, trim_echo_rebound=True",
            "negative": "only explicit no_chirp; tracked proposals; no positive unmatched penalty",
            "bootstrap": {"seed": SEED, "replicates": BOOTSTRAP_REPLICATES},
        },
        "heldout_test_wav_json_opened": args.partition == "test",
        "scientific_merge_authorized": False,
        "no_parameter_tuning": True,
    }, positives, negatives


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--dataset", type=Path, required=True)
    parser.add_argument("--processing", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--partition", choices=("calibration", "test"), required=True)
    parser.add_argument("--expected-evaluator-sha", required=True)
    parser.add_argument("--freeze-proof", type=Path)
    parser.add_argument("--request-nonce")
    args = parser.parse_args()
    dataset = args.dataset.resolve()
    output = args.output.resolve()
    require(not output.exists() and not output.is_relative_to(dataset)
            and not dataset.is_relative_to(output), "output overlaps frozen dataset")
    report, positives, negatives = compute(args)
    output.mkdir(parents=True, exist_ok=False)
    with (output / "report.json").open("x", encoding="utf-8") as f:
        json.dump(report, f, ensure_ascii=False, indent=2, allow_nan=False)
        f.write("\n")
    for name, data in (("positive_pairs.json", positives),
                       ("reviewed_negatives.json", negatives)):
        with (output / name).open("x", encoding="utf-8") as f:
            json.dump(data, f, ensure_ascii=False, indent=2, allow_nan=False)
            f.write("\n")
    # Never emit held-out recording paths or raw annotations into public CI logs.
    print("P31_RESULT: " + json.dumps({
        "status": report["status"], "partition": args.partition,
        "eligible_manual_refs": report["statistics"]["manual_references"],
        "reviewed_no_chirp_wavs": report["statistics"]["reviewed_negative_wavs"],
        "recording_groups": report["statistics"]["recording_groups"],
        "candidate_eligible_for_human_review":
            report["statistics"]["promotion_candidate_eligible_for_human_review"],
        "scientific_merge_authorized": False,
        "no_parameter_tuning": True,
    }, separators=(",", ":")), flush=True)


if __name__ == "__main__":
    main()
