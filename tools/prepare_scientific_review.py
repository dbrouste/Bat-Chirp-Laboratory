"""Export existing development diagnostics for human review; never run detection."""
from __future__ import annotations

import argparse
import copy
import csv
import html
import json
import math
from pathlib import Path
import shutil
import sqlite3
import sys

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
from laboratory import campaign_control as control
from laboratory.dataset import sha256_file, stable_hash
from laboratory.frozen_dataset import collect_frozen_dataset
from laboratory.pilot_selection import load_selection

QUEUES = ("no_blob", "nms", "end_error_top20", "negative_final")
DECISION_FIELDS = ("signal_classification", "annotation_assessment", "other_cause",
                   "evidence", "confidence", "reviewer", "reviewed_utc", "notes")


def read_json(path):
    return json.loads(Path(path).read_text(encoding="utf-8-sig"))


def write_json(path, data):
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("x", encoding="utf-8") as stream:
        json.dump(data, stream, ensure_ascii=False, indent=2, allow_nan=False)
        stream.write("\n")


def write_csv(path, rows, columns=None):
    columns = columns or list(dict.fromkeys(key for row in rows for key in row)) or ["case_id"]
    with path.open("x", encoding="utf-8-sig", newline="") as stream:
        writer = csv.DictWriter(stream, fieldnames=columns)
        writer.writeheader()
        for row in rows:
            writer.writerow({key: json.dumps(value, ensure_ascii=False, allow_nan=False)
                             if isinstance(value, (dict, list)) else value for key, value in row.items()})


def load_evidence(campaign):
    """Use the runner's validity gates and the persisted job receipt, without export_reports."""
    definition = control.load_definition(campaign)
    if not definition.get("diagnostic_probe") or not definition.get("selection_file"):
        raise ValueError("A selected diagnostic campaign is required")
    selection = load_selection(Path(definition["dataset"]), definition["selection_file"])
    if definition.get("test_access") or definition.get("partition") != "calibration":
        raise ValueError("Development only; held-out access is forbidden")
    control.verify_environment(definition)
    with sqlite3.connect((campaign / "campaign.sqlite").as_uri() + "?mode=ro", uri=True) as connection:
        state = connection.execute("SELECT status FROM detector_control WHERE singleton=1").fetchone()
        jobs = connection.execute("SELECT job_id,status,report_sha256 FROM detector_jobs ORDER BY ordinal").fetchall()
    if not state or state[0] != "complete" or len(jobs) != 1 or jobs[0][1] != "complete":
        raise ValueError("Exactly one complete baseline diagnostic is required")
    job_id, _, expected = jobs[0]
    if job_id != definition["experiments"][0]["job_id"]:
        raise ValueError("Unexpected baseline job")
    report_path = control.benchmark_path(campaign, job_id)
    if not expected or sha256_file(report_path) != expected:
        raise ValueError("Stored scientific report hash mismatch")
    report = control.validated_report(report_path, definition, definition["experiments"][0]["options"])
    records, _ = collect_frozen_dataset(Path(definition["dataset"]), selection_file=Path(definition["selection_file"]))
    with (Path(definition["dataset"]) / "manifest.csv").open(encoding="utf-8-sig", newline="") as stream:
        manifest = {row["dataset_wav"]: row for row in csv.DictReader(stream)}
    return definition, selection, records, manifest, report, report_path


def make_cases(report, records, sources, manifest, baseline_identity):
    """Select and reshape stored evidence only; no new scientific classification."""
    details = {(row["file"], str(row["chirp_id"])): row for row in report["details"]}
    cases, endpoints = [], []

    def add(queue, name, chirp_id, reason, metrics, automatic=None, evidence=None):
        if name not in sources or name not in records:
            raise ValueError("Review case outside the selected development inputs")
        source = sources[name]
        manual = None
        if chirp_id is not None:
            matches = [item for item in records[name].get("chirps", [])
                       if str(item.get("chirp_id")) == str(chirp_id)
                       and item.get("source") == "manual" and item.get("status") == "validated"]
            if len(matches) != 1 or not matches[0].get("points"):
                raise ValueError("Missing or ambiguous validated manual reference")
            manual = copy.deepcopy(matches[0])
        elif source["reference_class"] != "reviewed_no_chirp" or records[name].get("review_status") != "no_chirp":
            raise ValueError("Only explicitly reviewed no_chirp inputs may enter the negative queue")
        identity = {"baseline": baseline_identity, "queue": queue, "wav": name,
                    "wav_sha256": source["wav_sha256"], "manual": manual, "automatic": automatic}
        case_id = "review-v1-" + stable_hash(identity)[:24]
        case = {"case_id": case_id, "queue": queue, "cohort": "development", "file": name,
                "source_wav": source["source_wav"], "source_json": source["source_json"],
                "session": source["session"], "manual_chirp_id": chirp_id,
                "annotation": manifest[name]["dataset_json"], "manual_annotation": manual,
                "automatic": automatic, "reason": reason, "metrics": metrics,
                "evidence": evidence, "units": {"time": "ms", "frequency": "kHz"}}
        cases.append(case)
        return case

    for name, diagnostic in sorted(report["diagnostics"].items()):
        if name not in sources:
            raise ValueError("Diagnostic outside development selection")
        for row in diagnostic["failures"]:
            if row["category"] == "no_candidate_observed":
                add("no_blob", name, row["chirp_id"], row["reason"], details[name, str(row["chirp_id"])], evidence=row)
            elif row["last_stage"] == "time_frequency_nms":
                final_ids = {item["uid"] for item in diagnostic["stages"]["final"]}
                suppressed = [item for item in row["evidence"]["pre_nms"] if item["uid"] not in final_ids]
                if not suppressed:
                    raise ValueError("NMS case has no observed suppressed candidate")
                add("nms", name, row["chirp_id"], row["reason"], details[name, str(row["chirp_id"])],
                    evidence={"failure": row, "suppressed_candidates": suppressed,
                              "candidate_anchor_points": [{"uid": item["uid"],
                                  "t_ms": item["time_mid"] * 1000, "f_khz": item["peak_freq_hz"] / 1000,
                                  "representation": "stored_candidate_anchor_not_a_trajectory"} for item in suppressed],
                              "nms_input": diagnostic["stages"]["nms_input"],
                              "nms_final": diagnostic["stages"]["final"],
                              "suppressor_identity_recorded": False})
        endpoints.extend((name, row) for row in diagnostic["endpoints"])
        negative = diagnostic.get("negative")
        if negative is not None:
            if negative["status"] != "complete" or negative["accepted_final_trajectories"] != len(negative["accepted_curves"]):
                raise ValueError("Incomplete final negative trajectories")
            for curve in negative["accepted_curves"]:
                points = curve["points"]
                if len(points) < 2 or any(len(p) < 2 or not all(math.isfinite(v) for v in p) for p in points):
                    raise ValueError("Invalid stored negative curve")
                automatic = {"representation": "full_native_trajectory", "full_trajectory_available": True,
                    "native_points_s_hz_amplitude": points,
                    "points": [{"t_ms": p[0] * 1000, "f_khz": p[1] / 1000} for p in points],
                    "source_candidate_indices": curve["source_candidate_indices"],
                    "acceptance_scope": negative["scope"]}
                add("negative_final", name, None, "native_final_trajectory_on_reviewed_no_chirp",
                    {key: negative[key] for key in ("duration_seconds", "candidates", "accepted_final_trajectories")},
                    automatic=automatic, evidence={"tracking_traces": negative["tracking_traces"]})
    ranked = [(name, row) for name, row in endpoints if row.get("tracking_success")
              and row.get("end_abs_ms") is not None and math.isfinite(row["end_abs_ms"])]
    ranked.sort(key=lambda pair: (-pair[1]["end_abs_ms"], pair[0], str(pair[1]["chirp_id"])))
    for rank, (name, row) in enumerate(ranked[:20], 1):
        trace = row["trace"]
        automatic = {"representation": "endpoints_only_no_connecting_curve", "full_trajectory_available": False,
            "curve_hash": trace["curve_hash"], "points": [
                {"t_ms": trace["start_s"] * 1000, "f_khz": trace["start_hz"] / 1000},
                {"t_ms": trace["end_s"] * 1000, "f_khz": trace["end_hz"] / 1000}]}
        case = add("end_error_top20", name, row["chirp_id"], "largest_stored_absolute_end_error",
                   {**details[name, str(row["chirp_id"])], **{key: value for key, value in row.items() if key != "trace"}},
                   automatic=automatic, evidence=trace)
        case["rank"] = rank
    if len({case["case_id"] for case in cases}) != len(cases):
        raise ValueError("Duplicate review identity")
    return cases


def create_workspace(output, cases, records, dataset):
    """Physical copies, never hard links: all analyser writes stay in this export."""
    workspace = output / "analyser_workspace"
    rows, links = [], {}
    for name in sorted({case["file"] for case in cases}):
        token = stable_hash(name)[:20]
        wav_rel = f"calibration/wav/{token}/{Path(name).name}"
        snapshot_rel = f"calibration/annotations/{token}.json"
        wav = workspace / wav_rel
        working_json = wav.parent / f"{token}.json"
        wav.parent.mkdir(parents=True)
        shutil.copyfile(dataset / name, wav)
        record = copy.deepcopy(records[name])
        negatives = [case for case in cases if case["file"] == name and case["queue"] == "negative_final"]
        navigation = {}
        if negatives:
            record["chirps"] = []
            for case in negatives:
                # This is an automatic overlay ID, never a manual reference ID.
                overlay_id = int(case["case_id"].rsplit("-", 1)[1][:7], 16) + 1
                if overlay_id in navigation.values():
                    raise ValueError("Automatic overlay ID collision")
                navigation[case["case_id"]] = overlay_id
                record["chirps"].append({"chirp_id": overlay_id, "source": "automatic", "status": "draft",
                    "analysis_branch": "laboratory-review-snapshot", "review_case_id": case["case_id"],
                    "points": case["automatic"]["points"]})
        document = {"schema_version": 1, "units": {"time": "ms", "frequency": "kHz"},
                    "review_only_not_ground_truth": True, "files": {wav.name: record}}
        write_json(working_json, document)
        write_json(workspace / snapshot_rel, document)
        row = {"partition": "calibration", "session": next(case["session"] for case in cases if case["file"] == name),
               "dataset_wav": wav_rel, "dataset_json": snapshot_rel,
               "source_wav": str(wav.resolve()), "source_json": str(working_json.resolve()),
               "wav_sha256": sha256_file(wav), "json_sha256": sha256_file(working_json)}
        rows.append(row)
        links[name] = {"row": row, "overlay_ids": navigation}
    write_csv(workspace / "manifest.csv", rows)
    return workspace, links


def export_review(campaign, output, *, max_copy_gb=1.0, min_free_gb=2.0):
    campaign, output = Path(campaign).resolve(), Path(output).resolve()
    if output.exists():
        raise ValueError("Use a new export directory; existing reviews and decisions are never overwritten")
    if not all(math.isfinite(x) and x > 0 for x in (max_copy_gb, min_free_gb)):
        raise ValueError("Positive finite export disk budgets required")
    definition, selection, records, manifest, report, report_path = load_evidence(campaign)
    dataset = Path(definition["dataset"])
    sources = {row["file"]: row for row in selection["files"]}
    protected = [dataset, campaign, ROOT, ROOT.parent / "Bat-Acoustic-Analyser", Path(definition["processing_root"])]
    protected.extend(Path(row[field]).parent.resolve() for row in sources.values() for field in ("source_wav", "source_json"))
    if any(output == path or output.is_relative_to(path) or path.is_relative_to(output) for path in protected):
        raise ValueError("Export must be outside the corpus, sources, campaign and repositories")
    cases = make_cases(report, records, sources, manifest, definition["baseline_identity"])
    names = {case["file"] for case in cases}
    copy_bytes = sum((dataset / name).stat().st_size for name in names)
    if copy_bytes > max_copy_gb * 1024**3:
        raise ValueError("Review WAV copy budget exceeded")
    parent = output.parent
    while not parent.exists():
        parent = parent.parent
    if shutil.disk_usage(parent).free < copy_bytes + min_free_gb * 1024**3:
        raise ValueError("Insufficient free space for physical review copies and reserve")
    initial_hash = sha256_file(report_path)
    output.mkdir(parents=True)
    workspace, links = create_workspace(output, cases, records, dataset)
    counts = {queue: sum(case["queue"] == queue for case in cases) for queue in QUEUES}
    for case in cases:
        link = links[case["file"]]
        case["review_navigation_chirp_id"] = (case["manual_chirp_id"] if case["manual_chirp_id"] is not None
                                               else link["overlay_ids"][case["case_id"]])
        case["trace_file"] = f"traces/{stable_hash(case['file'])[:20]}.json"
        write_json(output / "cases" / f"{case['case_id']}.json", case)
    for name in names:
        if sha256_file(workspace / links[name]["row"]["dataset_wav"]) != sources[name]["wav_sha256"]:
            raise ValueError("Review WAV copy differs from source fingerprint")
        write_json(output / "traces" / f"{stable_hash(name)[:20]}.json", report["diagnostics"][name])
    for queue in QUEUES:
        members = [case for case in cases if case["queue"] == queue]
        write_csv(output / f"{queue}.csv", members)
        navigation = [{"case_id": case["case_id"], "wav": links[case["file"]]["row"]["dataset_wav"],
                       "annotation": links[case["file"]]["row"]["dataset_json"], "partition": "calibration",
                       "session": case["session"], "chirp_id": case["review_navigation_chirp_id"],
                       "manual_chirp_id": case["manual_chirp_id"], "review_priority": "review_suggested",
                       "reason": f"{queue}: {case['reason']} [{case['case_id']}]",
                       "source_wav_original": case["source_wav"], "source_json_original": case["source_json"],
                       "case_file": str(output / "cases" / f"{case['case_id']}.json")}
                      for case in members]
        write_csv(output / f"{queue}.analyser.csv", navigation)
    write_csv(output / "human_decisions.csv", [{"case_id": case["case_id"], "queue": case["queue"],
              **dict.fromkeys(DECISION_FIELDS, "")} for case in cases])
    write_json(output / "decision_schema.json", {
        "signal_classification": ["true_chirp", "artifact", "echo", "ambiguous", "other"],
        "annotation_assessment": ["correct", "annotation_error", "ambiguous", "not_applicable"],
        "confidence": ["low", "medium", "high"], "required_for_completed_review": ["reviewer", "reviewed_utc", "evidence"],
        "other_requires_documented_cause": True, "automatic_actions": False,
        "note": "Signal identity and annotation quality are independent decisions. Never relabel no_chirp automatically."})
    control.verify_environment(definition)
    if sha256_file(report_path) != initial_hash:
        raise ValueError("Scientific report changed during export")
    provenance = {"schema": "scientific_error_review_v1", "cohort": "development", "status": "complete",
        "scientific_calculations_executed": False, "campaign": str(campaign),
        "definition_fingerprint": definition["fingerprint"], "baseline_identity": definition["baseline_identity"],
        "scientific_code_fingerprint": definition["code_fingerprint"], "scientific_report_sha256": initial_hash,
        "selection_fingerprint": selection["fingerprint"], "exporter_sha256": sha256_file(Path(__file__)),
        "counts": counts, "matched_reference_pool": report["totals"]["matched"],
        "copied_wavs": len(names), "copied_wav_bytes": copy_bytes, "workspace_manifest": str(workspace / "manifest.csv"),
        "limits": ["Matched positive full automatic curves were not stored; only endpoints are available.",
                   "NMS removed-candidate membership is observed; the responsible suppressor was not traced.",
                   "Negative overlay IDs identify automatic drafts, not manual references or GUI acceptance.",
                   "GUI actions may write to disposable review copies; turn off folder automatic analysis.",
                   "No GUI read-only mode or graphical overlay validation is claimed."]}
    write_json(output / "review_cases.json", cases)
    page = "<!doctype html><html lang='fr'><meta charset='utf-8'><title>Revue scientifique</title>"
    page += "<h1>Revue scientifique de développement</h1><p>Copies de travail uniquement. Décisions : human_decisions.csv.</p>"
    page += "<p>Dans Analysis → Contrôle qualité, importer un CSV .analyser.csv et choisir analyser_workspace/manifest.csv. Désactiver l'analyse automatique du dossier avant ouverture.</p>"
    for queue in QUEUES:
        page += f"<h2>{queue} : {counts[queue]}</h2><ol>"
        for case in cases:
            if case["queue"] == queue:
                page += (f"<li><a href='cases/{case['case_id']}.json'>{case['case_id']}</a> — "
                         f"{html.escape(case['source_wav'])} — référence {html.escape(str(case['manual_chirp_id']))}</li>")
        page += "</ol>"
    page += "<p>Les négatifs utilisent des identifiants automatiques, jamais des références manuelles. Les points des candidats NMS et extrémités sont dans les annexes JSON, sans interpolation inventée.</p></html>"
    with (output / "index.html").open("x", encoding="utf-8") as stream:
        stream.write(page)
    provenance["files"] = {str(path.relative_to(output)): sha256_file(path)
                           for path in sorted(output.rglob("*")) if path.is_file()}
    provenance["mutable_working_files"] = ["human_decisions.csv", "analyser_workspace/calibration/wav/*/*.json"]
    write_json(output / "review_manifest.json", provenance)
    return provenance


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--campaign", required=True, type=Path)
    parser.add_argument("--output", required=True, type=Path)
    parser.add_argument("--max-copy-gb", type=float, default=1)
    parser.add_argument("--min-free-gb", type=float, default=2)
    args = parser.parse_args(argv)
    result = export_review(args.campaign, args.output, max_copy_gb=args.max_copy_gb, min_free_gb=args.min_free_gb)
    print(json.dumps({key: result[key] for key in ("counts", "copied_wavs", "copied_wav_bytes", "scientific_calculations_executed")}, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
