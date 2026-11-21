"""P2.4: full-cohort P0/P2 paired nonregression for extra-candidate gates.

Read-only pinned *calibration* evaluation: all 493 manual references and 203
explicit no_chirp WAVs are represented. Frozen P2 provides baseline trajectories
for 401 existing manual matches and 524 existing negative tracked proposals;
these are never modified. Every newly matched positive extra and every extra on
negative WAVs is independently re-tracked, with the P2.3 normal profile tested
on the extras surviving the 8kHz sweep criterion.

This is a *conservative additive gate*: old V4 detections are always retained;
a gated-out new extra cannot be credited as a manual match, even if another
candidate might someday substitute. Non-annotated candidates in positive WAVs
are NOT counted as negative. No train/test optimization, no scientific merge.
"""
from __future__ import annotations

import argparse
import csv
import json
import math
from collections import Counter, defaultdict
from pathlib import Path

from experiments.p21_artifact_diagnostics import (
    DETECTOR_REFERENCE, LAB_REFERENCE, MANIFEST_REFERENCE, P2_SHA,
    checked_path, csv_rows, curve_features, find_extra, hybrid_candidates,
    require, revision, sha256, track, validate_p2,
)
from experiments.p23_perpendicular_ridge import normal_profiles

# All diagnostic rules were fixed in P2.1/P2.3, before this complete audit.
# These still use the SAME calibration; not a generalization proof.
def passes(name, info):
    if name == "p2_all_extras":
        return bool(info["tracked"])
    if not info["tracked"] or info["track_sweep_khz"] < 8.0:
        return False
    if name == "sweep_ge_8khz":
        return True
    normal = info["normal"]
    require(normal is not None, "missing fixed-STFT perpendicular profile")
    if name == "sweep8_and_normal_contrast4_ge4":
        return normal["normal_contrast_4khz_median_db"] >= 4.0
    if name == "sweep8_and_normal_contrast8_ge6":
        return normal["normal_contrast_8khz_median_db"] >= 6.0
    if name == "sweep8_and_normal_center_dominant_ge60":
        return normal["normal_center_dominant_fraction"] >= .60
    if name == "sweep8_and_normal_energy_pm2_ge45":
        return normal["normal_pm2_energy_fraction_median"] >= .45
    raise ValueError("unknown predeclared exploratory diagnostic")


GATES = (
    "p2_all_extras",
    "sweep_ge_8khz",
    "sweep8_and_normal_contrast4_ge4",
    "sweep8_and_normal_contrast8_ge6",
    "sweep8_and_normal_center_dominant_ge60",
    "sweep8_and_normal_energy_pm2_ge45",
)


def boolean(text):
    require(text in ("True", "False"), "invalid P2 paired boolean field")
    return text == "True"


def reference_fraction(row):
    value = row["hybrid_tolerance_fraction"]
    return float(value) if value else 0.0


def analyze_extra(y, sr, candidate, bench, reference):
    curve = track(y, sr, candidate, bench, {
        "n_fft": None, "hop": None,
        "search_margin_ms": None, "trim_echo_rebound": True,
    })
    if curve is None:
        return {"tracked": False, "track_sweep_khz": None, "normal": None}
    geometry = curve_features(curve, candidate)
    sweep = geometry["track_frequency_drop_khz"]
    profile = normal_profiles(curve, reference) if sweep >= 8.0 else None
    return {"tracked": True, "track_sweep_khz": float(sweep),
            "normal": profile}


def evaluate_gate_records(positives, negatives, p2, gates=GATES):
    """Score fixed full cohort with existing P2 baseline preserved verbatim."""
    import numpy as np
    from benchmark.snr_metrics import detection_importance

    require(len(positives) == 493 and len(negatives) == 203,
            "missing full calibration rows")
    positive_by_id = {(r["file"], r["chirp_id"]): r for r in positives}
    require(len(positive_by_id) == 493, "duplicate manual reference")
    weights = {
        k: float(detection_importance(float(row["snr_db"]), min_weight=.2))
        for k, row in positive_by_id.items()
    }
    sum_weight = sum(weights.values())
    require(sum_weight > 0, "no reference weights")
    unfiltered = sum(weights[k] * reference_fraction(row)
                     for k, row in positive_by_id.items()) / sum_weight
    require(math.isclose(unfiltered,
                         p2["hybrid"]["weighted_full_pipeline_within_tolerance_fraction"],
                         abs_tol=1e-8),
            "P2 weighted scoring cannot be reconstructed from paired references")
    require(sum(boolean(r["hybrid_tracked"]) for r in positives) == 423,
            "P2 complete matched positive cohort changed")
    require(sum(int(n["baseline_tracked_proposals"]) for n in negatives) == 524
            and sum(int(n["added_tracked_proposals"]) for n in negatives) == 7,
            "P2 complete negative tracking cohort changed")
    summaries = {}
    for label in gates:
        accepted = {}
        dropped = []
        for key, row in positive_by_id.items():
            extra = row.get("_extra")
            accepted[key] = (
                True if extra is None else passes(label, extra)
            )
            if not accepted[key]:
                require(boolean(row["hybrid_matched"]),
                        "cannot reject a reference without a matched extra")
                dropped.append(key)
        accepted_score = sum(weights[k] * reference_fraction(row)
                             for k, row in positive_by_id.items()
                             if accepted[k]) / sum_weight
        require(accepted_score <= unfiltered + 1e-9,
                "postfilter unexpectedly improves a frozen score")
        additional_negative_tracks = sum(
            passes(label, proposal)
            for row in negatives for proposal in row.get("_extras", []))
        negative_tracked = 524 + additional_negative_tracks
        negative_wavs = sum(
            int(int(row["baseline_tracked_proposals"]) +
                sum(passes(label, proposal)
                    for proposal in row.get("_extras", [])) > 0)
            for row in negatives
        )
        snr_bands = {}
        for band, include in (
            ("low_le15", lambda x: x <= 15.0),
            ("medium_15_35", lambda x: 15.0 < x < 35.0),
            ("high_ge35", lambda x: x >= 35.0),
        ):
            selected = [k for k, row in positive_by_id.items()
                        if include(float(row["snr_db"]))]
            snr_bands[band] = {
                "refs": len(selected),
                "baseline_hybrid_tracked": sum(
                    boolean(positive_by_id[k]["hybrid_tracked"]) for k in selected),
                "gate_tracked": sum(
                    boolean(positive_by_id[k]["hybrid_tracked"]) and
                    accepted[k] for k in selected),
                "gate_rejected_refs": sum(not accepted[k] for k in selected),
            }
        summaries[label] = {
            "matched_manual_refs_after_gate_conservative": (
                423 - len(dropped)),
            "rejected_previously_matched_refs": len(dropped),
            "weighted_full_pipeline_score": accepted_score,
            "weighted_score_change_from_p2": accepted_score - unfiltered,
            "weighted_score_change_from_p0": (
                accepted_score -
                p2["baseline"]["weighted_full_pipeline_within_tolerance_fraction"]),
            "negative_tracked_proposals": negative_tracked,
            "added_negative_tracked_proposals_vs_v4": additional_negative_tracks,
            "negative_wavs_with_tracked_proposals": negative_wavs,
            "snr": snr_bands,
            "preserves_existing_v4_seeds_by_construction": True,
            "independent_validation": False,
            "scientific_merge_authorized": False,
        }
    return summaries


def compute(args):
    import soundfile as sf
    import bat_analysis.detection as detector
    from benchmark import reassignment_detector_seeds as bench
    from benchmark.snr_metrics import ReferenceSNR
    from laboratory.runner import remove_excluded_references

    dataset, processing, review = (
        args.dataset.resolve(), args.processing.resolve(),
        args.review_project.resolve())
    require(revision(processing) == DETECTOR_REFERENCE and
            Path(detector.__file__).resolve().is_relative_to(processing) and
            Path(bench.__file__).resolve().is_relative_to(processing),
            "unpinned scientific detector checkout")
    require(sha256(dataset / "manifest.csv") == MANIFEST_REFERENCE,
            "frozen dataset manifest drift")
    p1 = json.loads(args.p1.read_text(encoding="utf-8"))
    p2 = json.loads((args.p2 / "report.json").read_text(encoding="utf-8"))
    validate_p2(p2, p1, review)
    p23 = json.loads((args.p23 / "report.json").read_text(encoding="utf-8"))
    require(p23["status"] == "complete" and
            p23["dataset_manifest_sha256"] == MANIFEST_REFERENCE and
            p23["p0_detector_sha"] == DETECTOR_REFERENCE and
            p23["p2_experiment_sha"] == P2_SHA and
            p23["p1_decisions_sha256"] == p1["decisions_sha256"] and
            p23["positive_tracks"] == 22 and
            p23["remaining_artifact_tracks"] == 4 and
            p23["scientific_merge_authorized"] is False and
            p23["heldout_test_wav_json_opened"] is False,
            "P2.3 scientific result changed")
    previous_p23 = csv_rows(args.p23 / "normal_profile_cases.csv")
    p23_by_id = {x["case_id"]: x for x in previous_p23}
    require(len(previous_p23) == 26 and len(p23_by_id) == 26,
            "P2.3 individual cases incomplete")
    cases = json.loads((review / "review_cases.json").read_text(encoding="utf-8"))
    require(len(cases) == 35 and len({x["case_id"] for x in cases}) == 35,
            "P1 human review case index incomplete")
    pos_cases = {
        (x["input_dataset_wav"], str(x["manual_chirp_id"])): x
        for x in cases if x["review_category"] == "review_manually_annotated_chirp"
    }
    neg_cases = defaultdict(list)
    for case in cases:
        if case["review_category"] == "review_candidate_on_no_chirp_wav":
            neg_cases[case["input_dataset_wav"]].append(case)
    require(len(pos_cases) == 22 and
            sum(len(v) for v in neg_cases.values()) == 13,
            "P1 labels not identical to P2.3 cohort")
    positives = csv_rows(args.p2 / "positive_trajectory_pairs.csv")
    negatives = csv_rows(args.p2 / "negative_tracked_proposals.csv")
    require(len(positives) == 493 and len(negatives) == 203,
            "missing P2 full calibration rows")
    pairs_by_file = defaultdict(list)
    for row in positives:
        pairs_by_file[row["file"]].append(row)
    neg_by_wav = {row["wav"]: row for row in negatives}
    require(len(pairs_by_file) == 37 and len(neg_by_wav) == 203,
            "unexpected positive/negative P2 WAV cohort")
    manifest_rows = csv_rows(dataset / "manifest.csv")
    calibration = {r["dataset_wav"]: r for r in manifest_rows
                   if r["partition"] == "calibration"}
    require(len(calibration) == 428, "calibration manifest changed")
    require(set(pairs_by_file).isdisjoint(neg_by_wav)
            and all(name.startswith("calibration/wav/")
                    for name in (*pairs_by_file.keys(), *neg_by_wav.keys())),
            "P2 cohort contains duplicates or test paths")
    details = []
    added_positive_unmatched = 0
    positive_extra_matched = 0
    for count, (name, rows) in enumerate(sorted(pairs_by_file.items()), 1):
        meta = calibration[name]
        wav = checked_path(dataset, name, "calibration/wav/")
        ann = checked_path(dataset, meta["dataset_json"],
                           "calibration/annotations/")
        require(sha256(wav).lower() == meta["wav_sha256"].lower()
                and sha256(ann).lower() == meta["json_sha256"].lower(),
                "positive calibration WAV/JSON modified")
        doc = json.loads(ann.read_text(encoding="utf-8-sig"))
        record = remove_excluded_references(
            {name: doc["files"][wav.name]})[0][name]
        manual = bench._manual_annotations(record)
        require(record.get("review_status") != "no_chirp" and
                len(manual) == len(rows), "positive cohort annotation count drift")
        by_chirp = {r["chirp_id"]: r for r in rows}
        require(len(by_chirp) == len(rows), "positive reference id duplicated")
        y, sr = sf.read(wav, always_2d=False)
        if y.ndim == 2:
            import numpy as np
            y = np.mean(y, axis=1)
        baseline, hybrid, extras = hybrid_candidates(y, int(sr), detector)
        links = {j: i for i, j in bench.match_candidates(
            hybrid, manual, geometry_mode="square_normals_v1")}
        added_positive_unmatched += len(extras)
        ref = None
        for j, entry in enumerate(manual):
            key = str(entry[0])
            require(key in by_chirp and
                    boolean(by_chirp[key]["hybrid_matched"]) == (j in links),
                    "P2 matched chirp pairing drift")
            if j not in links:
                continue
            index = links[j]
            if index < len(baseline):
                continue
            row = by_chirp[key]
            require(boolean(row["hybrid_tracked"]),
                    "matched P2 extra unexpectedly untracked")
            if ref is None:
                ref = ReferenceSNR.from_audio(y, int(sr))
            candidate = hybrid[index]
            result = analyze_extra(y, int(sr), candidate, bench, ref)
            require(result["tracked"], "P2 positive extra no longer tracked")
            row["_extra"] = result
            positive_extra_matched += 1
            case = pos_cases.get((name, key))
            if case is not None:
                k, extra = find_extra(case, extras)
                require(index == len(baseline) + k and
                        result["track_sweep_khz"] >= 8.,
                        "reviewed rescued chirp is not the assigned P2 candidate")
                prior = p23_by_id[case["case_id"]]
                require(prior["cohort"] == "rescued_positive" and
                        math.isclose(
                            result["normal"]["normal_contrast_4khz_median_db"],
                            float(prior["normal_contrast_4khz_median_db"]),
                            abs_tol=1e-4),
                        "P2.3 rescue normal measurements drifted")
            details.append({
                "cohort": "annotated_positive_matched_extra",
                "wav": name, "chirp_id": key,
                "review_case_id": case["case_id"] if case else "",
                "session": meta["session"],
                "snr_db": float(row["snr_db"]),
                **{gate: passes(gate, result) for gate in GATES},
            })
        if count % 10 == 0 or count == 37:
            print("P24_PROGRESS_POSITIVE: wavs=" + str(count) + "/37",
                  flush=True)
    require(positive_extra_matched >= 22 and
            positive_extra_matched <= p2["differences"]["added_positive_candidates"],
            "positive extra candidate association drift")
    added_positive_unmatched -= positive_extra_matched
    require(added_positive_unmatched >= 0, "invalid unmatched positive proposal count")
    extra_neg_tracks = 0
    negative_extra_count = 0
    negative_extra_reviewed = 0
    for count, (name, row) in enumerate(sorted(neg_by_wav.items()), 1):
        row["_extras"] = []
        if int(row["added_candidates"]) == 0:
            continue
        meta = calibration[name]
        wav = checked_path(dataset, name, "calibration/wav/")
        ann = checked_path(dataset, meta["dataset_json"],
                           "calibration/annotations/")
        require(sha256(wav).lower() == meta["wav_sha256"].lower()
                and sha256(ann).lower() == meta["json_sha256"].lower(),
                "no_chirp calibration input fingerprint drift")
        record = json.loads(ann.read_text(encoding="utf-8-sig"))["files"][wav.name]
        record = remove_excluded_references({name: record})[0][name]
        require(record.get("review_status") == "no_chirp"
                and not bench._manual_annotations(record),
                "no_chirp negative label changed")
        y, sr = sf.read(wav, always_2d=False)
        if y.ndim == 2:
            import numpy as np
            y = np.mean(y, axis=1)
        baseline, hybrid, extras = hybrid_candidates(y, int(sr), detector)
        require(len(extras) == int(row["added_candidates"]) and
                len(baseline) == int(row["baseline_candidates"]),
                "P2 negative candidate identities changed")
        require(len(neg_cases[name]) == len(extras),
                "P1 negative human review not exhaustive over added proposals")
        case_by_index = {}
        for case in neg_cases[name]:
            index, unused = find_extra(case, extras)
            require(index not in case_by_index, "P1 negative case duplicate")
            case_by_index[index] = case
        require(len(case_by_index) == len(extras), "P1 negative case missing")
        ref = ReferenceSNR.from_audio(y, int(sr))
        for index, candidate in enumerate(extras):
            result = analyze_extra(y, int(sr), candidate, bench, ref)
            row["_extras"].append(result)
            negative_extra_count += 1
            extra_neg_tracks += int(result["tracked"])
            case = case_by_index[index]
            negative_extra_reviewed += 1
            if result["tracked"] and result["track_sweep_khz"] >= 8.:
                prior = p23_by_id[case["case_id"]]
                require(prior["cohort"] == "remaining_tracked_artifact" and
                        math.isclose(
                            result["normal"]["normal_contrast_4khz_median_db"],
                            float(prior["normal_contrast_4khz_median_db"]),
                            abs_tol=1e-4),
                        "P2.3 negative normal measurements drifted")
            details.append({
                "cohort": "reviewed_no_chirp_added_proposal",
                "wav": name, "chirp_id": "",
                "review_case_id": case["case_id"],
                "session": meta["session"],
                "snr_db": "",
                **{gate: passes(gate, result) for gate in GATES},
            })
        require(sum(int(x["tracked"]) for x in row["_extras"]) ==
                int(row["added_tracked_proposals"]),
                "P2 negative tracked proposal count changed")
        if count % 40 == 0:
            print("P24_PROGRESS_NEGATIVE: wavs=" + str(count) + "/203",
                  flush=True)
    print("P24_PROGRESS_NEGATIVE: wavs=203/203", flush=True)
    require(extra_neg_tracks == 7 and negative_extra_count == 13 and
            negative_extra_reviewed == 13,
            "full P2 negative cohort not reconstructed")
    by_gate = evaluate_gate_records(positives, negatives, p2)
    require(by_gate["p2_all_extras"]["matched_manual_refs_after_gate_conservative"] == 423
            and by_gate["p2_all_extras"]["negative_tracked_proposals"] == 531
            and by_gate["sweep_ge_8khz"]["negative_tracked_proposals"] == 528
            and by_gate["sweep_ge_8khz"]["matched_manual_refs_after_gate_conservative"] == 423,
            "P2/P2.1 full-cohort consistency check failed")
    for name in GATES:
        case_pos = sum(r[name] for r in details
                       if r["review_case_id"] and
                       r["cohort"] == "annotated_positive_matched_extra")
        require(case_pos == by_gate[name]["matched_manual_refs_after_gate_conservative"] -
                (423 - positive_extra_matched),
                "full-cohort positive scores inconsistent with per-case flags")
        case_neg = sum(r[name] for r in details
                       if r["cohort"] == "reviewed_no_chirp_added_proposal")
        require(case_neg == by_gate[name]["added_negative_tracked_proposals_vs_v4"],
                "full-cohort negative scores inconsistent with per-case flags")
    summary = {
        "schema_version": 1, "status": "complete",
        "scope": "P2.4 complete 493 annotated / 203 reviewed no_chirp calibration cohort",
        "dataset_manifest_sha256": MANIFEST_REFERENCE,
        "p0_detector_sha": DETECTOR_REFERENCE, "p2_experiment_sha": P2_SHA,
        "p1_decisions_sha256": p1["decisions_sha256"],
        "manual_references": 493,
        "annotated_positive_wavs": 37,
        "reviewed_negative_wavs": 203,
        "previously_matched_refs": 401,
        "p2_matched_refs": 423,
        "p2_positive_extra_associations": positive_extra_matched,
        "positive_extra_candidates_unmatched_to_an_annotation": added_positive_unmatched,
        "reviewed_extra_negative_candidates": 13,
        "reviewed_extra_negative_tracked": 7,
        "fixed_gate_full_calibration_results": by_gate,
        "no_new_model_selected": True,
        "independent_validation_done": False,
        "heldout_test_wav_json_opened": False,
        "scientific_merge_authorized": False,
        "methodology": (
            "All 493 P2 paired positive references and 203 reviewed no_chirp WAVs "
            "are scored. Every newly matched positive extra and every negative "
            "extra is re-tracked and its P2.3 normal profile evaluated. The 401 "
            "existing positive matches and baseline 524 negative tracked proposals "
            "are pinned from completed P2 and retained by construction, not "
            "retracked in this stage. Rejected extras count conservatively as "
            "unmatched; no rematching for alternative seeds. Unmatched added "
            "candidates in positive WAVs are not false positives. Fixed thresholds "
            "are same-calibration exploratory and require independent validation."
        ),
    }
    return summary, details


def main():
    p = argparse.ArgumentParser()
    for key in ("dataset", "p1", "p2", "p23", "review-project",
                "processing", "output"):
        p.add_argument("--" + key, type=Path, required=True)
    args = p.parse_args()
    output = args.output.resolve()
    require(not output.exists() and not output.is_relative_to(args.dataset.resolve())
            and not output.is_relative_to(args.review_project.resolve())
            and not output.is_relative_to(args.p2.resolve())
            and not output.is_relative_to(args.p23.resolve()),
            "P2.4 output must be fresh and disjoint from scientific inputs")
    report, candidates = compute(args)
    output.mkdir(parents=True, exist_ok=False)
    fields = ("cohort", "wav", "chirp_id", "review_case_id",
              "session", "snr_db", *GATES)
    with (output / "candidate_gate_effects.csv").open(
        "w", newline="", encoding="utf-8") as f:
        writer = csv.DictWriter(f, fieldnames=fields)
        writer.writeheader()
        writer.writerows(candidates)
    (output / "report.json").write_text(
        json.dumps(report, ensure_ascii=False, indent=2, allow_nan=False) + "\n",
        encoding="utf-8")
    compact = {
        "status": report["status"],
        "positive_matched_extras": report["p2_positive_extra_associations"],
        "unscored_added_positive_candidates":
            report["positive_extra_candidates_unmatched_to_an_annotation"],
        "gates": {
            name: {
                "refs_matched": d["matched_manual_refs_after_gate_conservative"],
                "negative_tracks": d["negative_tracked_proposals"],
                "weighted_full_pipeline": d["weighted_full_pipeline_score"],
            } for name, d in report["fixed_gate_full_calibration_results"].items()
        },
        "science_merge_authorized": False,
    }
    print("P24_FULL_CALIBRATION_RESULT: " + json.dumps(
        compact, ensure_ascii=False, separators=(",", ":")), flush=True)


if __name__ == "__main__":
    main()
