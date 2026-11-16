"""Evidence-qualified detection failures and endpoint distributions from traces."""

from collections import Counter, defaultdict
import argparse
import csv
import html
import json
from pathlib import Path

import numpy as np
from scipy.stats import rankdata

from . import campaign_control as control
from .detection_probe import canonical
from .pilot_selection import load_selection


def snr_class(value):
    return "unknown" if value is None else "low_15_or_less" if value <= 15 else "medium_15_to_35" if value < 35 else "high_35_or_more"


def distribution(values):
    values = np.asarray([value for value in values if value is not None], dtype=float)
    if not len(values):
        return {"count": 0}
    return {"count": len(values), "mean": float(np.mean(values)), "mean_abs": float(np.mean(np.abs(values))),
        **{key: float(np.percentile(values, percentile)) for key, percentile in
           (("min", 0), ("p05", 5), ("p25", 25), ("median", 50), ("p75", 75), ("p95", 95), ("max", 100))},
        "negative": int(np.sum(values < 0)), "zero": int(np.sum(values == 0)), "positive": int(np.sum(values > 0))}


def relationship(rows, feature, target):
    pairs = [(row[feature], row[target]) for row in rows if row.get(feature) is not None and row.get(target) is not None]
    if len(pairs) < 2:
        return {"count": len(pairs), "pearson": None, "spearman": None}
    values = np.asarray(pairs, dtype=float)
    if not np.ptp(values[:, 0]) or not np.ptp(values[:, 1]):
        return {"count": len(pairs), "pearson": None, "spearman": None}
    return {"count": len(pairs), "pearson": float(np.corrcoef(values.T)[0, 1]),
            "spearman": float(np.corrcoef(rankdata(values[:, 0]), rankdata(values[:, 1]))[0, 1]),
            "interpretation": "descriptive_only_not_causal_no_independence_assumed"}


def save_csv(path, rows):
    columns = list(dict.fromkeys(key for row in rows for key in row)) or ["file"]
    with path.open("w", encoding="utf-8-sig", newline="") as stream:
        writer = csv.DictWriter(stream, fieldnames=columns)
        writer.writeheader()
        writer.writerows({key: json.dumps(value, ensure_ascii=False) if isinstance(value, (dict, list)) else value
                         for key, value in row.items()} for row in rows)


def create_report(folder, pilot_report_path):
    folder, pilot_report_path = Path(folder).resolve(), Path(pilot_report_path).resolve()
    definition = control.load_definition(folder)
    campaign = control.export_reports(folder)
    if campaign["status"] != "complete" or not definition.get("diagnostic_probe"):
        raise ValueError("A complete diagnostic campaign is required")
    report = control.validated_report(control.benchmark_path(folder, campaign["jobs"][0]["job_id"]),
                                      definition, definition["experiments"][0]["options"])
    previous = json.loads(pilot_report_path.read_text(encoding="utf-8"))
    for key in ("input_fingerprints", "options", "algorithm_git"):
        if report["metadata"].get(key) != previous["metadata"].get(key):
            raise ValueError(f"Diagnostic pilot provenance changed: {key}")
    for key in ("totals", "per_file", "details"):
        if canonical(report[key]) != canonical(previous[key]):
            raise ValueError(f"Diagnostic campaign changed pilot scientific results: {key}")
    selection = load_selection(Path(definition["dataset"]), definition["selection_file"])
    source_rows = {row["file"]: row for row in selection["files"]}
    failures, endpoints, negatives, review, negative_review = [], [], [], [], []
    manifest = {}
    with (Path(definition["dataset"]) / "manifest.csv").open(encoding="utf-8-sig", newline="") as stream:
        manifest = {row["dataset_wav"]: row for row in csv.DictReader(stream)}
    for name, diagnostic in report["diagnostics"].items():
        source = source_rows[name]
        common = {"source_wav": source["source_wav"], "source_json": source["source_json"],
                  "frozen_wav": str(Path(definition["dataset"]) / name), "cohort": "development"}
        for row in diagnostic["failures"]:
            failures.append({**row, **common, "snr_class": snr_class(row["snr_max_db"])})
            review.append({"wav": name, "annotation": manifest[name]["dataset_json"], "partition": "calibration",
                "session": source["session"], "chirp_id": row["chirp_id"], "review_priority": "review_suggested",
                "reason": row["reason"], **common})
        endpoints.extend({**row, **common, "snr_class": snr_class(row["snr_max_db"])} for row in diagnostic["endpoints"])
        if diagnostic["negative"] is not None:
            negative = {**diagnostic["negative"], **common}
            negatives.append(negative)
            for index, curve in enumerate(negative["accepted_curves"]):
                negative_review.append({"file": name, "trajectory_id": f"diagnostic-final:{index}",
                    "manual_chirp_id": None, **curve, **common})
    expected_misses = report["totals"]["annotated"] - report["totals"]["matched"]
    if len(failures) != expected_misses:
        raise ValueError("Not every unmatched reference has a diagnostic row")
    by_snr = {}
    for label in ("low_15_or_less", "medium_15_to_35", "high_35_or_more"):
        members = [row for row in failures if row["snr_class"] == label]
        by_snr[label] = {"missed": len(members), "categories": dict(Counter(row["category"] for row in members))}
    signed = {field: distribution(row.get(field) for row in endpoints) for field in
              ("start_signed_ms", "end_signed_ms", "start_abs_ms", "end_abs_ms")}
    endpoint_by_snr = {label: {field: distribution(row.get(field) for row in endpoints if row["snr_class"] == label)
                      for field in signed} for label in by_snr}
    relationships = {feature: {target: relationship(endpoints, feature, target) for target in signed}
                     for feature in ("snr_max_db", "duration_ms", "f_min_khz", "f_max_khz")}
    negative_summary = {key: sum(row[key] for row in negatives if row[key] is not None) for key in
        ("duration_seconds", "candidates", "tracking_rejected", "tracked_candidates", "accepted_final_trajectories")}
    negative_summary.update(wavs=len(negatives), wavs_with_accepted_final=sum((row["accepted_final_trajectories"] or 0) > 0 for row in negatives),
        scope="native reassignment tracking plus chirp_union finite-curve validation/grouping; no GUI/biological acceptance claimed")
    negative_summary["final_measured_wavs"] = sum(row["status"] == "complete" for row in negatives)
    negative_summary["final_measured_audio_seconds"] = sum(row["duration_seconds"] for row in negatives if row["status"] == "complete")
    negative_summary["final_pipeline_errors"] = [{"file": row["file"], "error": row["error"]}
                                                for row in negatives if row["status"] != "complete"]
    if negative_summary["final_pipeline_errors"]:
        negative_summary["accepted_final_trajectories_in_measured_subset"] = negative_summary["accepted_final_trajectories"]
        negative_summary["accepted_final_trajectories"] = None
    negative_summary["tracking_stop_evidence"] = dict(Counter(
        ((trace.get("ridge") or {}).get("diagnostics") or {}).get("stop_reason")
        or " | ".join((trace.get("ridge") or {}).get("source_guards", [])) or "indeterminate_return"
        for row in negatives for trace in row["tracking_traces"] if not trace["accepted"]))
    negative_summary["GUI_final_accepted_trajectories"] = None
    summary = {"schema": "detection_failure_report_v1", "pilot_scientific_results_unchanged": True,
        "references": report["totals"]["annotated"], "matched": report["totals"]["matched"], "missed": len(failures),
        "categories": dict(Counter(row["category"] for row in failures)), "by_snr": by_snr,
        "endpoint_distributions": signed, "endpoint_by_snr": endpoint_by_snr, "relationships": relationships,
        "signed_convention": "automatic minus manual; start negative=early, end positive=late",
        "negative_final_outputs": negative_summary, "global_resources": campaign.get("global_supervision"),
        "equivalence": {name: diagnostic["equivalence"] for name, diagnostic in report["diagnostics"].items()},
        "limits": ["Matching precedes tracking in this benchmark; missed references were not tracked.",
                   "A raw admissible blob is not proof of biological identity.",
                   "No overlapping bbox does not establish absence of acoustic signal.",
                   "GUI final acceptance is a separate, unexecuted pipeline.",
                   "Low SNR absent; development exploratory; no threshold optimization."],
        "failures": failures, "endpoints": endpoints, "negative_wavs": negatives}
    control.runner._write_json_atomic(folder / "failure_report.json", summary)
    save_csv(folder / "detection_failures.csv", failures)
    save_csv(folder / "endpoint_errors.csv", endpoints)
    save_csv(folder / "negative_final_outputs.csv", [{key: value for key, value in row.items()
              if key not in {"groups", "tracking_traces", "accepted_curves"}} for row in negatives])
    save_csv(folder / "annotation_review_queue.csv", review)
    save_csv(folder / "negative_trajectory_review.csv", negative_review)
    document = "<!doctype html><html lang='fr'><meta charset='utf-8'><title>Diagnostic des detections</title>"
    document += "<style>body{font:16px sans-serif;margin:2em}pre{white-space:pre-wrap}td,th{border:1px solid #ccc;padding:.4em}</style><h1>Diagnostic exploratoire</h1>"
    for title, value in ((f"{len(failures)} manques : categories observees", summary["categories"]), ("Par SNR", by_snr),
                         ("Sorties finales negatives : portee limitee au pipeline reassigne", negative_summary),
                         ("Erreurs temporelles", signed), ("Relations descriptives", relationships)):
        document += f"<h2>{html.escape(title)}</h2><pre>{html.escape(json.dumps(value, ensure_ascii=False, indent=2))}</pre>"
    document += "<h2>Limites</h2><ul>" + "".join(f"<li>{html.escape(value)}</li>" for value in summary["limits"]) + "</ul>"
    document += "<p>Les CSV de revue reemploient les chemins et identifiants manuels existants. Les trajectoires negatives ont des identifiants derives, jamais manuels.</p></html>"
    control.atomic_text(folder / "failure_report.html", document)
    return summary


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--campaign", type=Path, required=True)
    parser.add_argument("--pilot-report", type=Path, required=True)
    args = parser.parse_args(argv)
    summary = create_report(args.campaign, args.pilot_report)
    print(json.dumps({key: summary[key] for key in ("missed", "categories", "negative_final_outputs")}, indent=2))


if __name__ == "__main__":
    main()
