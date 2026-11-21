"""P2.5: grouped calibration robustness and paired uncertainty, no new detector.

This analysis is deliberately report-only. It reads the pinned frozen
calibration manifest and P2/P2.4 reports, never any WAV or annotation JSON
(including held-out test). Fixed thresholds are inherited from prior phases;
nothing is optimized, trained, deployed or scientifically merged.

Grouping is by manifest session where informative, otherwise by frozen
annotation bundle. These groups are NOT newly independent test data, and
cluster-bootstrap intervals are descriptive uncertainty on REUSED calibration.
"""
from __future__ import annotations

import argparse
import csv
import json
import math
import random
from collections import defaultdict
from pathlib import Path

from experiments.p24_full_calibration_gates import (
    DETECTOR_REFERENCE, MANIFEST_REFERENCE, P2_SHA, GATES,
    boolean, csv_rows, reference_fraction, require, sha256,
)

BOOTSTRAP_SEED = 42
BOOTSTRAP_ITERATIONS = 2000


def select_grouping(calibration):
    sessions = {str(row.get("session") or "").strip() for row in calibration.values()}
    sessions.discard("")
    # If all recordings share a generic session, annotation bundles provide
    # smaller *descriptive* groups, NOT independent sampling units.
    use_session = len(sessions) >= 3
    def key(row):
        return (str(row["session"]) if use_session
                else str(row["dataset_json"]))
    return ("manifest_session" if use_session else "annotation_bundle",
            key)


def empty_group():
    return {
        "manual_refs": 0, "no_chirp_wavs": 0,
        "reference_weight_sum": 0., "p0_weighted_mass": 0.,
        "p2_weighted_mass": 0.,
        "negative_duration_seconds": 0.,
        "baseline_negative_tracks": 0,
        "gate_weighted_masses": {g: 0. for g in GATES},
        "gate_negative_tracks": {g: 0 for g in GATES},
        "gate_matched_refs": {g: 0 for g in GATES},
    }


def score_groups(group_map, keys=None):
    """Pair group-weighted recall-quality with negative tracked proposals/h."""
    selected = (list(group_map) if keys is None else list(keys))
    denom = sum(group_map[k]["reference_weight_sum"] for k in selected)
    hours = sum(group_map[k]["negative_duration_seconds"] for k in selected) / 3600.
    out = {"manual_refs": sum(group_map[k]["manual_refs"] for k in selected),
           "reviewed_negative_wavs": sum(group_map[k]["no_chirp_wavs"] for k in selected),
           "p0_score": None, "p2_score": None, "gates": {}}
    if denom > 0:
        out["p0_score"] = sum(group_map[k]["p0_weighted_mass"]
                              for k in selected) / denom
        out["p2_score"] = sum(group_map[k]["p2_weighted_mass"]
                              for k in selected) / denom
    for gate in GATES:
        mass = sum(group_map[k]["gate_weighted_masses"][gate] for k in selected)
        neg = sum(group_map[k]["gate_negative_tracks"][gate] for k in selected)
        baseline = sum(group_map[k]["baseline_negative_tracks"] for k in selected)
        score = mass / denom if denom > 0 else None
        out["gates"][gate] = {
            "weighted_full_pipeline_score": score,
            "delta_vs_p0": (score-out["p0_score"]
                            if score is not None and out["p0_score"] is not None
                            else None),
            "delta_vs_p2": (score-out["p2_score"]
                            if score is not None and out["p2_score"] is not None
                            else None),
            "negative_tracks": neg,
            "extra_negative_tracks_vs_v4": neg-baseline,
            "negative_tracks_per_hour": neg/hours if hours > 0 else None,
            "extra_negative_tracks_per_hour": (
                (neg-baseline)/hours if hours > 0 else None),
            "matched_refs_conservative": sum(
                group_map[k]["gate_matched_refs"][gate] for k in selected),
        }
    return out


def confidence_interval(xs):
    values = sorted(float(v) for v in xs if v is not None and math.isfinite(float(v)))
    if not values:
        return {"n": 0, "p025": None, "median": None, "p975": None}
    def quantile(q):
        x = (len(values)-1)*q
        i = int(x)
        j = min(i+1, len(values)-1)
        return values[i] + (values[j] - values[i])*(x-i)
    return {
        "n": len(values), "p025": quantile(.025),
        "median": quantile(.5), "p975": quantile(.975),
    }


def grouped_bootstrap(groups, *, iterations=BOOTSTRAP_ITERATIONS, seed=BOOTSTRAP_SEED):
    require(1 <= iterations <= 10000 and isinstance(seed, int),
            "invalid bounded bootstrap parameters")
    keys = sorted(groups)
    require(keys, "empty recording-group distribution")
    rng = random.Random(seed)
    values = {gate: {"delta_vs_p0": [], "delta_vs_p2": [],
                     "extra_negative_tracks_per_hour": []}
              for gate in GATES}
    for iteration in range(iterations):
        chosen = rng.choices(keys, k=len(keys))
        score = score_groups(groups, chosen)
        for gate in GATES:
            result = score["gates"][gate]
            for metric in values[gate]:
                values[gate][metric].append(result[metric])
    return {
        "cluster_count": len(keys),
        "iterations": iterations,
        "seed": seed,
        "intervals": {
            gate: {metric: confidence_interval(series)
                   for metric, series in metrics.items()}
            for gate, metrics in values.items()},
    }


def compute(args):
    from benchmark.snr_metrics import detection_importance

    dataset = args.dataset.resolve()
    require(sha256(dataset / "manifest.csv") == MANIFEST_REFERENCE,
            "frozen calibration dataset changed")
    p2 = json.loads((args.p2 / "report.json").read_text(encoding="utf-8"))
    p24 = json.loads((args.p24 / "report.json").read_text(encoding="utf-8"))
    require(p2["status"] == "complete" and
            p2["dataset_manifest_sha256"] == MANIFEST_REFERENCE and
            p2["p0_detector_sha"] == DETECTOR_REFERENCE and
            p2["p2_experiment_sha"] == P2_SHA and
            p2["manual_references"] == 493 and
            p2["reviewed_negative_wavs"] == 203 and
            p2["scientific_scope"].startswith("exploratory") and
            p2["gate"]["authorized_for_scientific_merge"] is False,
            "P2 prior scientific baseline not pinned")
    require(p24["status"] == "complete" and
            p24["dataset_manifest_sha256"] == MANIFEST_REFERENCE and
            p24["p0_detector_sha"] == DETECTOR_REFERENCE and
            p24["p2_experiment_sha"] == P2_SHA and
            p24["p1_decisions_sha256"] == p2["p1_human_decisions_sha256"] and
            p24["manual_references"] == 493 and
            p24["reviewed_negative_wavs"] == 203 and
            p24["p2_positive_extra_associations"] == 22 and
            p24["reviewed_extra_negative_candidates"] == 13 and
            p24["reviewed_extra_negative_tracked"] == 7 and
            p24["scientific_merge_authorized"] is False and
            p24["heldout_test_wav_json_opened"] is False and
            p24["independent_validation_done"] is False,
            "P2.4 report insufficiently pinned")
    pairs = csv_rows(args.p2 / "positive_trajectory_pairs.csv")
    neg_wavs = csv_rows(args.p2 / "negative_tracked_proposals.csv")
    extras = csv_rows(args.p24 / "candidate_gate_effects.csv")
    require(len(pairs) == 493 and len(neg_wavs) == 203 and
            len(extras) >= 35, "P2/P2.4 detailed cohort incomplete")
    add_pos = {
        (row["wav"], row["chirp_id"]): row
        for row in extras if row["cohort"] == "annotated_positive_matched_extra"
    }
    add_neg = defaultdict(list)
    for row in extras:
        if row["cohort"] == "reviewed_no_chirp_added_proposal":
            add_neg[row["wav"]].append(row)
    require(len(add_pos) == p24["p2_positive_extra_associations"]
            and sum(map(len, add_neg.values())) == 13,
            "P2.4 added candidate detail reconciliation failed")
    manifest_rows = csv_rows(dataset / "manifest.csv")
    calibration = {r["dataset_wav"]: r for r in manifest_rows
                   if r["partition"] == "calibration"}
    require(len(calibration) == 428, "unexpected frozen calibration membership")
    grouping_mode, key = select_grouping(calibration)
    groups = defaultdict(empty_group)
    for row in pairs:
        wav = row["file"]
        require(wav in calibration and wav.startswith("calibration/wav/"),
                "manual reference outside calibration")
        session = key(calibration[wav])
        group = groups[session]
        weight = float(detection_importance(float(row["snr_db"]), min_weight=.2))
        prior = float(row["baseline_tolerance_fraction"] or 0.)
        hybrid = reference_fraction(row)
        require(0 <= prior <= 1 and 0 <= hybrid <= 1 and weight > 0,
                "invalid frozen manual reference score")
        group["manual_refs"] += 1
        group["reference_weight_sum"] += weight
        group["p0_weighted_mass"] += weight*prior
        group["p2_weighted_mass"] += weight*hybrid
        match = boolean(row["hybrid_matched"])
        new = add_pos.get((wav, row["chirp_id"]))
        require(new is None or match, "P2.4 extra attached to unmatched manual ref")
        for gate in GATES:
            accepted = new is None or boolean(new[gate])
            group["gate_weighted_masses"][gate] += weight*hybrid*int(accepted)
            group["gate_matched_refs"][gate] += int(match and accepted)
    for row in neg_wavs:
        wav = row["wav"]
        require(wav in calibration and wav.startswith("calibration/wav/"),
                "reviewed negative outside calibration")
        session = key(calibration[wav])
        group = groups[session]
        original = int(row["baseline_tracked_proposals"])
        measured = int(row["added_tracked_proposals"])
        proposals = add_neg.get(wav, [])
        require(len(proposals) == int(row["added_candidates"])
                and sum(boolean(p["p2_all_extras"]) for p in proposals) == measured,
                "reviewed negative candidate replay mismatch")
        group["no_chirp_wavs"] += 1
        group["baseline_negative_tracks"] += original
        group["negative_duration_seconds"] += float(row["duration_seconds"])
        for gate in GATES:
            group["gate_negative_tracks"][gate] += (
                original + sum(boolean(p[gate]) for p in proposals))
    require(sum(g["manual_refs"] for g in groups.values()) == 493 and
            sum(g["no_chirp_wavs"] for g in groups.values()) == 203,
            "grouped calibration cohort incomplete")
    overall = score_groups(groups)
    require(math.isclose(overall["p0_score"],
                         p2["baseline"]["weighted_full_pipeline_within_tolerance_fraction"],
                         abs_tol=1e-8) and
            math.isclose(overall["p2_score"],
                         p2["hybrid"]["weighted_full_pipeline_within_tolerance_fraction"],
                         abs_tol=1e-8),
            "P0/P2 weighted reference score reconstruction changed")
    for gate in GATES:
        expected = p24["fixed_gate_full_calibration_results"][gate]
        observed = overall["gates"][gate]
        require(observed["matched_refs_conservative"] ==
                expected["matched_manual_refs_after_gate_conservative"] and
                observed["negative_tracks"] == expected["negative_tracked_proposals"] and
                math.isclose(observed["weighted_full_pipeline_score"],
                             expected["weighted_full_pipeline_score"], abs_tol=1e-8),
                "P2.5 session accounting disagrees with P2.4 full-cohort score")
    by_group = {
        key: score_groups(groups, [key])
        for key in sorted(groups)
    }
    leave_one_out = {
        key: score_groups(groups, [k for k in groups if k != key])
        for key in sorted(groups)
    } if len(groups) >= 2 else {}
    uncertainty = grouped_bootstrap(groups)
    summary = {
        "schema_version": 1, "status": "complete",
        "scope": "P2.5 grouped sensitivity on reused calibration, no independent test",
        "dataset_manifest_sha256": MANIFEST_REFERENCE,
        "p0_detector_sha": DETECTOR_REFERENCE,
        "p2_experiment_sha": P2_SHA,
        "grouping_mode": grouping_mode,
        "recording_group_count": len(groups),
        "manual_references": 493,
        "reviewed_negative_wavs": 203,
        "overall": overall,
        "by_recording_group": by_group,
        "leave_one_group_out": leave_one_out,
        "grouped_bootstrap": uncertainty,
        "inference_limitations": (
            "Entirely reused calibration, threshold hypotheses previously explored "
            "on these same recordings; cluster resampling is descriptive only, "
            "not independent generalization validation. Grouping based on manifest "
            "session (if 3+ distinct) otherwise annotation-bundle clusters, which "
            "may not be independent biological sessions. No positive unannotated "
            "candidates are scored as false, and no heldout test WAV/JSON is opened."
        ),
        "independent_validation_done": False,
        "scientific_merge_authorized": False,
        "heldout_test_wav_json_opened": False,
    }
    return summary


def main():
    p = argparse.ArgumentParser()
    for name in ("dataset", "p2", "p24", "output"):
        p.add_argument("--" + name, type=Path, required=True)
    args = p.parse_args()
    output = args.output.resolve()
    require(not output.exists() and not output.is_relative_to(args.dataset.resolve())
            and not output.is_relative_to(args.p2.resolve())
            and not output.is_relative_to(args.p24.resolve()),
            "P2.5 result must not write into dataset/previous studies")
    summary = compute(args)
    output.mkdir(parents=True, exist_ok=False)
    (output / "report.json").write_text(
        json.dumps(summary, ensure_ascii=False, indent=2, allow_nan=False) + "\n",
        encoding="utf-8")
    safe = {
        "status": summary["status"],
        "grouping_mode": summary["grouping_mode"],
        "recording_group_count": summary["recording_group_count"],
        "overall": summary["overall"],
        "scientific_merge_authorized": False,
    }
    print("P25_GROUPED_ROBUSTNESS: " + json.dumps(
        safe, ensure_ascii=False, separators=(",", ":")), flush=True)
    print("P25_BOOTSTRAP_INTERVALS: " + json.dumps(
        summary["grouped_bootstrap"], separators=(",", ":")), flush=True)


if __name__ == "__main__":
    main()
