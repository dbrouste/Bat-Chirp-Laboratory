"""Calibrate geometry recognition thresholds on human-labelled curve pairs.

Input JSON: {"examples": [{"id": ..., "group": "recording-night-id",
  "split": "development"|"validation"|"test", "expected_good": true|false,
  "snr_max_db": 40, "manual_points": [[t_ms, f_khz], ...],
  "automatic_points": [[t_ms, f_khz], ...]}]}

Only development examples select parameters. Validation is reported once for
the chosen configuration. Test examples are NEVER scored or opened by this
command (even their points can be omitted). Different splits cannot share a
group. Manual references are never changed.
"""

from __future__ import annotations

import argparse
from dataclasses import asdict, replace
from itertools import product
import json
from pathlib import Path

from .geometry import GeometryConfig, importance_weight, score_pair


RADIUS = (1.5, 2.0, 2.5)
TAIL_ANGLE = (15.0, 20.0, 30.0)
MIN_COVERAGE = (0.30, 0.40, 0.50)
MAX_MEDIAN_ERROR = (0.75, 1.0, 1.25)
MIN_SCORE = (10.0, 25.0, 40.0, 55.0)


def read_examples(path: Path) -> list[dict]:
    document = json.loads(path.read_text(encoding="utf-8"))
    examples = document.get("examples") if isinstance(document, dict) else None
    if not isinstance(examples, list) or not examples:
        raise ValueError("Expected a non-empty 'examples' list")
    allowed = {"development", "validation", "test"}
    ids = set()
    groups = {}
    for example in examples:
        if not isinstance(example, dict) or example.get("split") not in allowed:
            raise ValueError("Example has no valid split")
        if not example.get("id") or example["id"] in ids:
            raise ValueError("Every calibration example needs a unique id")
        ids.add(example["id"])
        group = example.get("group")
        if not isinstance(group, str) or not group.strip():
            raise ValueError("Every example needs a recording-session group")
        previous = groups.setdefault(group, example["split"])
        if previous != example["split"]:
            raise ValueError(f"Session group {group!r} leaks across splits")
        if example["split"] != "test":
            if type(example.get("expected_good")) is not bool:
                raise ValueError("expected_good must be a boolean")
            for field in ("manual_points", "automatic_points", "snr_max_db"):
                if field not in example:
                    raise ValueError(f"Missing {field} in {example['id']}")
    if not any(e["split"] == "development" for e in examples):
        raise ValueError("At least one development example is required")
    return examples


def _metrics(rows: list[tuple[dict, dict]], parameters: tuple[float, float, float]):
    min_cover, max_error, min_score = parameters
    tp = fp = fn = tn = 0.0
    high_good = high_found = 0.0
    for example, result in rows:
        median = result["median_normalized_error"]
        predicted = (result["coverage"] >= min_cover and median is not None
                     and median <= max_error and result["score"] > min_score)
        expected = example["expected_good"]
        weight = importance_weight(example["snr_max_db"])
        if expected and example["snr_max_db"] >= 35:
            high_good += weight
            if predicted:
                high_found += weight
        if expected and predicted:
            tp += weight
        elif not expected and predicted:
            fp += weight
        elif expected and not predicted:
            fn += weight
        else:
            tn += weight
    precision = tp / (tp + fp) if tp + fp else 0.0
    recall = tp / (tp + fn) if tp + fn else 0.0
    return {"weighted_f1": 2 * precision * recall / (precision + recall)
            if precision + recall else 0.0,
            "weighted_precision": precision,
            "weighted_recall": recall,
            "high_snr_recall": high_found / high_good if high_good else None,
            "tp_weight": tp, "fp_weight": fp, "fn_weight": fn,
            "tn_weight": tn, "examples": len(rows)}


def _score_examples(examples: list[dict], config: GeometryConfig):
    # This is the expensive step. Reuse every geometric result over the much
    # larger threshold grid below, so we do not redo normals and intersections.
    rows = []
    for example in examples:
        result = score_pair(example["manual_points"],
                            example["automatic_points"],
                            example["snr_max_db"], config)
        rows.append((example, {"score": result["score"],
                               "coverage": result["coverage"],
                               "median_normalized_error": result[
                                   "median_normalized_error"]}))
    return rows


def calibrate(examples: list[dict], base: GeometryConfig = GeometryConfig()) -> dict:
    groups = {name: [e for e in examples if e["split"] == name]
              for name in ("development", "validation", "test")}
    if not any(e["expected_good"] for e in groups["development"]):
        raise ValueError("Development set needs positive examples")
    if not any(not e["expected_good"] for e in groups["development"]):
        raise ValueError("Development set needs negative examples")

    best = None
    searched = 0
    for radius, angle in product(RADIUS, TAIL_ANGLE):
        config = replace(base, search_radius_factor=radius, tail_angle_deg=angle)
        rows = _score_examples(groups["development"], config)
        for cover, error, score in product(MIN_COVERAGE, MAX_MEDIAN_ERROR, MIN_SCORE):
            metrics = _metrics(rows, (cover, error, score))
            searched += 1
            # Tie break toward fewer false positives and tighter thresholds.
            ranking = (metrics["weighted_f1"],
                       metrics["high_snr_recall"] or 0,
                       -metrics["fp_weight"], score, cover, -radius, -angle)
            if best is None or ranking > best[0]:
                best = (ranking, replace(config, min_match_coverage=cover,
                                        max_match_median_normalized_error=error,
                                        min_match_score=score), metrics)
    _, chosen, development_metrics = best
    validation_metrics = (_metrics(_score_examples(groups["validation"], chosen),
                                   (chosen.min_match_coverage,
                                    chosen.max_match_median_normalized_error,
                                    chosen.min_match_score))
                          if groups["validation"] else None)
    return {"protocol": "geometry-calibration-v1",
            "selection_metric": "SNR-weighted F1 (development only)",
            "tested_combinations": searched,
            "config": asdict(chosen),
            "development": development_metrics,
            "validation": validation_metrics,
            "sealed_test_examples": len(groups["test"]),
            "note": "Test examples were neither evaluated nor used for selection. "
                    "Confirm calibrated values on a representative real corpus."}


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--examples", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args(argv)
    result = calibrate(read_examples(args.examples))
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(json.dumps(result, ensure_ascii=False, indent=2),
                           encoding="utf-8")
    print(json.dumps({"config": result["config"],
                      "development": result["development"],
                      "validation": result["validation"]},
                     ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
