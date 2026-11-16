"""Readable pilot summaries from existing benchmark results, without audio access."""

import argparse
import csv
import html
import json
from pathlib import Path
from statistics import fmean

from . import campaign_control as control
from .autonomous_optimizer import _cases
from .pilot_selection import load_selection


def mean_field(details, field):
    values = [row[field] for row in details if row.get(field) is not None]
    return fmean(values) if values else None


def write_report(folder):
    folder = Path(folder).resolve()
    definition = control.load_definition(folder)
    campaign = control.export_reports(folder)
    selection = load_selection(Path(definition["dataset"]), definition["selection_file"])
    if len(campaign["jobs"]) != 1 or not definition.get("global_memory"):
        raise ValueError("Pilot report requires one baseline and global memory supervision")
    resources = campaign.get("global_supervision", [])
    if campaign["status"] == "complete" and (not resources or any(
            row.get("scope") != "supervisor_and_all_descendant_workers_commit_memory"
            or row.get("job_memory_limit_verified") is not True or row.get("process_tree_assigned") is not True
            or not row.get("peak_job_commit_bytes")
            or row.get("memory_limit_bytes") != definition["limits"]["memory_bytes"]
            or row["peak_job_commit_bytes"] > row["memory_limit_bytes"] for row in resources)):
        raise ValueError("Global memory enforcement has not been verified")
    summary = {"schema": "development_real_pilot_report_v1", "status": campaign["status"],
        "selection": {key: selection[key] for key in ("fingerprint", "requested", "available", "selected")},
        "baseline_identity": definition["baseline_identity"], "global_resources": campaign.get("global_supervision", []),
        "limits": definition["limits"], "warnings": control.WARNINGS,
        "incidents": [row for row in campaign["attempts"] if row["status"] != "complete"],
        "final_false_trajectories": None, "final_false_trajectories_reason": "Infrastructure measures detector candidates only",
        "scientific_results": None}
    cases = []
    rows = []
    if campaign["status"] == "complete":
        job = campaign["jobs"][0]
        report = control.validated_report(control.benchmark_path(folder, job["job_id"]), definition,
                                          definition["experiments"][0]["options"])
        files = {row["file"]: row for row in selection["files"]}
        if set(files) != {row["file"] for row in report["per_file"]}:
            raise ValueError("Pilot results do not cover exactly the selected WAVs")
        summary["scientific_results"] = report["totals"]
        summary["time_errors_ms"] = {field: mean_field(report["details"], field)
                                     for field in ("start_error_ms", "end_error_ms")}
        summary["scientific_job_seconds"] = job["measured_duration_s"]
        summary["baseline_report_sha256"] = job["report_sha256"]
        rows.append({"scope": "all", "annotated": report["totals"]["annotated"],
            "matched": report["totals"]["matched"], "recall": report["totals"]["matched"] / report["totals"]["annotated"],
            "geometry_khz_eq": report["totals"]["mean_geometric_error_khz_eq"],
            "frequency_error_khz": report["totals"]["mean_tracking_error_khz"],
            "start_error_ms": summary["time_errors_ms"]["start_error_ms"], "end_error_ms": summary["time_errors_ms"]["end_error_ms"]})
        for name, group in report["totals"]["by_snr"].items():
            rows.append({"scope": name, "annotated": group["annotated"], "matched": group["detector_matched"],
                "recall": group["detector_recall"], "geometry_khz_eq": group["mean_geometric_error_khz_eq"],
                "frequency_error_khz": group["mean_tracking_error_khz"], "start_error_ms": group["mean_start_error_ms"],
                "end_error_ms": group["mean_end_error_ms"]})
        cases = _cases(report)
        for row in report["per_file"]:
            if files[row["file"]]["reference_class"] == "reviewed_no_chirp" and row.get("candidates", 0) > 0:
                cases.append({"file": row["file"], "chirp_id": "", "issues": "candidate_alert_in_reviewed_no_chirp",
                              "priority": 90, "negative_candidates": row["candidates"]})
        for case in cases:
            source = files[case["file"]]
            case.update(source_wav=source["source_wav"], source_json=source["source_json"],
                        frozen_wav=str(Path(definition["dataset"]) / case["file"]))
        cases.sort(key=lambda row: (-row["priority"], row["file"], str(row.get("chirp_id"))))
    summary["manual_review_cases"] = cases
    control.runner._write_json_atomic(folder / "pilot_report.json", summary)
    for name, records in (("pilot_metrics.csv", rows), ("manual_review_cases.csv", cases)):
        fields = sorted({key for row in records for key in row}) or ["file"]
        with (folder / name).open("w", newline="", encoding="utf-8-sig") as stream:
            writer = csv.DictWriter(stream, fieldnames=fields)
            writer.writeheader()
            writer.writerows(records)
    document = "<!doctype html><html lang='fr'><meta charset='utf-8'><title>Pilote reel de developpement</title>"
    document += "<style>body{font:16px sans-serif;margin:2em}table{border-collapse:collapse;width:100%}td,th{border:1px solid #ccc;padding:.5em;overflow-wrap:anywhere}</style>"
    document += f"<h1>Pilote reel exploratoire</h1><p>Statut : {html.escape(summary['status'])}</p>"
    document += f"<p>Demandes : {selection['requested']} ; disponibles : {selection['available']} ; selectionnes : {selection['selected']}.</p>"
    document += "<p>Developpement uniquement. Aucun arbitrage algorithmique. Faibles SNR sous-representes ; validation historique limitee a une session.</p>"
    if rows:
        fields = list(rows[0])
        document += "<h2>Metriques existantes</h2><table><tr>" + "".join(f"<th>{field}</th>" for field in fields) + "</tr>"
        for row in rows:
            document += "<tr>" + "".join(f"<td>{html.escape(str(row[field]))}</td>" for field in fields) + "</tr>"
        document += "</table><p>Descriptifs seulement : aucune conclusion sur les classes absentes ou sous-representees.</p>"
        document += "<h2>Alertes candidates negatives</h2><pre>" + html.escape(json.dumps(summary["scientific_results"]["reviewed_no_chirp"], indent=2)) + "</pre>"
    document += "<p>Fausses trajectoires finales : non mesurables dans le benchmark actuel.</p>"
    document += "<h2>Ressources globales</h2><pre>" + html.escape(json.dumps(summary["global_resources"], indent=2)) + "</pre>"
    document += "<h2>Cas a inspecter</h2><table><tr><th>Priorite</th><th>Motif</th><th>WAV original</th><th>Chirp</th></tr>"
    for case in cases:
        source = case["source_wav"]
        link = Path(source).as_uri() if source and Path(source).is_absolute() else ""
        document += f"<tr><td>{case['priority']}</td><td>{html.escape(case['issues'])}</td><td><a href='{html.escape(link)}'>{html.escape(source)}</a></td><td>{html.escape(str(case.get('chirp_id')))}</td></tr>"
    control.atomic_text(folder / "pilot_report.html", document + "</table></html>")
    return summary


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--campaign", required=True, type=Path)
    args = parser.parse_args(argv)
    summary = write_report(args.campaign)
    print(json.dumps({"status": summary["status"], "manual_cases": len(summary["manual_review_cases"])}, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
