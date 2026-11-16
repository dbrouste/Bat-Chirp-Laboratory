"""Deterministic development-only selections, without copying corpus data."""

from __future__ import annotations

import argparse
from collections import defaultdict
import csv
import json
from pathlib import Path

from .dataset import sha256_file, stable_hash
from .frozen_dataset import _within

SCHEMA = "development_pilot_selection_v1"


def snapshot_metadata(dataset, snapshot_path):
    snapshot = json.loads(Path(snapshot_path).read_text(encoding="utf-8"))
    payload = {key: value for key, value in snapshot.items() if key != "snapshot_id"}
    if (snapshot.get("schema") != "chirp_cohort_snapshot_v1"
            or stable_hash(payload) != snapshot.get("snapshot_id")
            or snapshot.get("manifest_sha256") != sha256_file(dataset / "manifest.csv")):
        raise ValueError("Snapshot or manifest identity mismatch")
    with (dataset / "manifest.csv").open(encoding="utf-8-sig", newline="") as stream:
        rows = list(csv.DictReader(stream))
    by_name = {row["dataset_wav"].replace("\\", "/"): row for row in rows}
    if len(by_name) != len(rows):
        raise ValueError("Duplicate manifest WAV paths")
    assignments = {row["dataset_wav"]: row for row in snapshot["assignments"]}
    if set(assignments) != set(by_name):
        raise ValueError("Snapshot membership mismatch")
    for name, row in by_name.items():
        if any(assignments[name].get(field) != row.get(field) for field in
               ("partition", "session", "dataset_json", "wav_sha256", "json_sha256")):
            raise ValueError("Snapshot and manifest records disagree")
    return snapshot, by_name, assignments


def load_selection(dataset, selection_file):
    selection = json.loads(Path(selection_file).read_text(encoding="utf-8"))
    payload = {key: value for key, value in selection.items() if key != "fingerprint"}
    if selection.get("schema") != SCHEMA or stable_hash(payload) != selection.get("fingerprint"):
        raise ValueError("Selection integrity failure")
    snapshot, _, assignments = snapshot_metadata(dataset, selection["snapshot_path"])
    if sha256_file(Path(selection["snapshot_path"])) != selection["snapshot_sha256"]:
        raise ValueError("Snapshot file changed")
    if snapshot["manifest_sha256"] != selection["manifest_sha256"]:
        raise ValueError("Selection manifest mismatch")
    files = selection["files"]
    names = {row["file"] for row in files}
    if not names or len(names) != len(files):
        raise ValueError("Empty or duplicate selection")
    for row in files:
        assignment = assignments.get(row["file"], {})
        if (assignment.get("cohort") != "development" or assignment.get("partition") != "calibration"
                or row["reference_class"] not in {"positive_partial", "reviewed_no_chirp"}):
            raise ValueError("Selection must contain development references only")
    return selection


def reference_class(record):
    curves = record.get("chirps", [])
    if not isinstance(curves, list) or any(not isinstance(curve, dict) for curve in curves):
        raise ValueError("Invalid chirp list")
    if record.get("review_status") == "no_chirp":
        if any(curve.get("source") == "manual" or curve.get("status") == "validated"
               or curve.get("edited") for curve in curves):
            raise ValueError("Contradictory no_chirp label")
        return "reviewed_no_chirp"
    if any(curve.get("source") == "manual" and curve.get("status") == "validated"
           and not (isinstance(curve.get("annotation_quality"), dict)
                    and curve["annotation_quality"].get("status") == "excluded") for curve in curves):
        return "positive_partial"
    return "unknown"


def balanced(rows, count, seed):
    buckets = defaultdict(list)
    for row in rows:
        buckets[(row["session"], row["snr_stratum"])].append(row)
    for values in buckets.values():
        values.sort(key=lambda row: stable_hash([seed, row["file"]]))
    keys = sorted(buckets, key=lambda key: stable_hash([seed, key]))
    chosen = []
    while len(chosen) < count:
        added = False
        for key in keys:
            if buckets[key] and len(chosen) < count:
                chosen.append(buckets[key].pop(0))
                added = True
        if not added:
            break
    return chosen


def prepare_selection(dataset, snapshot_path, output, *, positives=20, negatives=20,
                      snr_report=None, seed="development-real-pilot-v1"):
    dataset, output = Path(dataset).resolve(), Path(output).resolve()
    if positives < 1 or negatives < 1:
        raise ValueError("Positive selection counts required")
    if output.exists() or output.is_relative_to(dataset):
        raise ValueError("Use a new selection directory outside the corpus")
    snapshot, manifest, assignments = snapshot_metadata(dataset, snapshot_path)
    evidence = json.loads(Path(snr_report).read_text(encoding="utf-8")) if snr_report else None
    if evidence and ((evidence["metadata"].get("frozen_dataset") or {}).get("manifest_sha256") != snapshot["manifest_sha256"]
                     or evidence["metadata"].get("status") != "complete"):
        raise ValueError("Historical SNR report does not match the manifest")
    snrs = defaultdict(list)
    if evidence:
        for detail in evidence["details"]:
            if assignments.get(detail["file"], {}).get("cohort") == "development" and detail.get("snr_max_db") is not None:
                snrs[detail["file"]].append(detail["snr_max_db"])
    documents = {}
    candidates = []
    for name, assignment in sorted(assignments.items()):
        if assignment["cohort"] != "development":
            continue
        row = manifest[name]
        annotation = _within(dataset, row["dataset_json"])
        if not annotation.is_relative_to(dataset / "calibration" / "annotations"):
            raise ValueError("Development annotation path escapes calibration")
        if annotation not in documents:
            if sha256_file(annotation) != row["json_sha256"]:
                raise ValueError("Development annotation hash mismatch")
            documents[annotation] = json.loads(annotation.read_text(encoding="utf-8-sig"))["files"]
        record = documents[annotation][Path(name).name]
        label = reference_class(record)
        if label == "unknown":
            continue
        bands = sorted({"low" if value <= 15 else "medium" if value < 35 else "high" for value in snrs[name]})
        candidates.append({"file": name, "session": row["session"], "reference_class": label,
            "snr_stratum": "+".join(bands) if bands else "unknown", "source_wav": row.get("source_wav", ""),
            "source_json": row.get("source_json", ""), "wav_sha256": row["wav_sha256"], "json_sha256": row["json_sha256"]})
    positive_rows = [row for row in candidates if row["reference_class"] == "positive_partial"]
    negative_rows = [row for row in candidates if row["reference_class"] == "reviewed_no_chirp"]
    selected = balanced(positive_rows, positives, seed) + balanced(negative_rows, negatives, seed)
    selection = {"schema": SCHEMA, "seed": seed, "cohort": "development", "exploratory_only": True,
        "snapshot_path": str(Path(snapshot_path).resolve()), "snapshot_sha256": sha256_file(Path(snapshot_path)),
        "manifest_sha256": snapshot["manifest_sha256"], "requested": {"positive": positives, "negative": negatives},
        "available": {"positive": len(positive_rows), "negative": len(negative_rows)},
        "selected": {"positive": min(positives, len(positive_rows)), "negative": min(negatives, len(negative_rows))},
        "historical_snr_sha256": sha256_file(Path(snr_report)) if snr_report else None,
        "snr_use": "historical_development_stratification_only_not_current_results", "files": selected}
    selection["fingerprint"] = stable_hash(selection)
    output.mkdir(parents=True)
    path = output / "selection.json"
    path.write_text(json.dumps(selection, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    with (output / "selection.csv").open("w", newline="", encoding="utf-8-sig") as stream:
        writer = csv.DictWriter(stream, fieldnames=list(selected[0]) if selected else ["file"])
        writer.writeheader()
        writer.writerows(selected)
    load_selection(dataset, path)
    return selection


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--dataset", required=True, type=Path)
    parser.add_argument("--snapshot", required=True, type=Path)
    parser.add_argument("--output", required=True, type=Path)
    parser.add_argument("--snr-report", type=Path)
    parser.add_argument("--positives", type=int, default=20)
    parser.add_argument("--negatives", type=int, default=20)
    args = parser.parse_args(argv)
    selection = prepare_selection(args.dataset, args.snapshot, args.output, positives=args.positives,
                                  negatives=args.negatives, snr_report=args.snr_report)
    print(json.dumps({key: selection[key] for key in ("requested", "available", "selected", "fingerprint")}, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
