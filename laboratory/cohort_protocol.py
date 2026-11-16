"""Draft cohort snapshots and descriptive quality reports, without audio analysis."""

from __future__ import annotations

import argparse
from collections import Counter, defaultdict
import csv
import json
import math
from pathlib import Path
import re
from statistics import median

from .aggregation import summarize_by_snr
from .dataset import sha256_file, stable_hash
from .dataset_builder import verify_annotation_format
from .fingerprints import implementation_fingerprint
from .frozen_dataset import _within
from .quality_audit import inspect_chirp, wav_header
from .runner import remove_excluded_references

ROOT = Path(__file__).resolve().parents[1]
DEFAULT_PROTOCOL = ROOT / "experiments" / "scientific_protocol_v1.json"
COHORTS = ("development", "validation", "final_test_reserved")


def read_protocol(path: Path) -> dict:
    protocol = json.loads(Path(path).read_text(encoding="utf-8"))
    expected = json.loads(DEFAULT_PROTOCOL.read_text(encoding="utf-8"))
    if set(protocol) != set(expected):
        raise ValueError("Unsupported protocol fields")
    for field in set(expected) - {"seed", "validation_groups"}:
        if protocol[field] != expected[field]:
            raise ValueError(f"Unsupported protocol policy: {field}")
    if (not isinstance(protocol["seed"], str) or not protocol["seed"]
            or type(protocol["validation_groups"]) is not int
            or protocol["validation_groups"] < 1):
        raise ValueError("Invalid seed or validation group count")
    return protocol


def read_manifest(dataset: Path) -> list[dict]:
    with (dataset / "manifest.csv").open(encoding="utf-8-sig", newline="") as stream:
        reader = csv.DictReader(stream)
        required = {"partition", "session", "dataset_wav", "dataset_json", "wav_sha256", "json_sha256"}
        if not required.issubset(reader.fieldnames or []):
            raise ValueError("Missing manifest columns")
        rows = list(reader)
    if not rows:
        raise ValueError("Empty manifest")
    seen = set()
    session_names = {}
    for row in rows:
        if row["partition"] not in {"calibration", "test"} or not row["session"]:
            raise ValueError("Invalid partition or session")
        session = row["session"].replace("\\", "/").casefold()
        if session in session_names and session_names[session] != row["session"]:
            raise ValueError("Ambiguous session spelling")
        session_names[session] = row["session"]
        for field in ("dataset_wav", "dataset_json"):
            row[field] = row[field].replace("\\", "/")
            _within(dataset, row[field])
            folder = "wav" if field == "dataset_wav" else "annotations"
            if not row[field].casefold().startswith(f"{row['partition']}/{folder}/"):
                raise ValueError("Manifest path and partition disagree")
        key = row["dataset_wav"].casefold()
        if key in seen:
            raise ValueError("Duplicate WAV path")
        seen.add(key)
        for field in ("wav_sha256", "json_sha256"):
            row[field] = row[field].lower()
            if not re.fullmatch(r"[0-9a-f]{64}", row[field]):
                raise ValueError("Missing or invalid content hash")
    return sorted(rows, key=lambda row: row["dataset_wav"])


def assign_cohorts(rows: list[dict], protocol: dict) -> list[dict]:
    parents = {row["session"]: row["session"] for row in rows}

    def find(session):
        while parents[session] != session:
            parents[session] = parents[parents[session]]
            session = parents[session]
        return session

    linked = {}
    for row in rows:
        for kind, value in (("audio", row["wav_sha256"]),
                            ("json", row["dataset_json"].casefold())):
            key = (kind, value)
            if key in linked:
                first, second = find(linked[key]), find(row["session"])
                parents[max(first, second)] = min(first, second)
            linked[key] = row["session"]
    partitions = defaultdict(set)
    for row in rows:
        partitions[find(row["session"])].add(row["partition"])
    if any(len(values) != 1 for values in partitions.values()):
        raise ValueError("Session or duplicate audio crosses calibration/test boundary")
    groups = [group for group, values in partitions.items() if values == {"calibration"}]
    groups.sort(key=lambda group: stable_hash([protocol["seed"], group]))
    if len(groups) <= protocol["validation_groups"]:
        raise ValueError("Insufficient independent calibration groups for development and validation")
    validation = set(groups[:protocol["validation_groups"]])
    return [{**row, "session_group": find(row["session"]),
             "cohort": ("final_test_reserved" if row["partition"] == "test"
                        else "validation" if find(row["session"]) in validation
                        else "development")} for row in rows]


def describe(values) -> dict:
    values = sorted(value for value in values if isinstance(value, (int, float))
                    and math.isfinite(value))
    return {"count": len(values), "min": min(values) if values else None,
            "median": median(values) if values else None,
            "max": max(values) if values else None}


def inspect_calibration(dataset: Path, assignments: list[dict]):
    documents = {}
    related = defaultdict(list)
    for row in assignments:
        if row["partition"] == "calibration":
            related[row["dataset_json"]].append(row)
    for name, rows in related.items():
        path = _within(dataset, name)
        if len({row["json_sha256"] for row in rows}) != 1 or sha256_file(path) != rows[0]["json_sha256"]:
            raise ValueError("Annotation hash mismatch")
        document = json.loads(path.read_text(encoding="utf-8-sig"))
        verify_annotation_format(document, path)
        expected = [Path(row["dataset_wav"]).name for row in rows]
        if len(set(expected)) != len(expected) or set(expected) != set(document["files"]):
            raise ValueError("Manifest and JSON membership disagree")
        documents[name] = document["files"]
    wavs, chirps, findings, records = [], [], [], {}
    for row in assignments:
        if row["partition"] == "test":
            continue
        name = row["dataset_wav"]
        record = documents[row["dataset_json"]][Path(name).name]
        if not isinstance(record, dict) or not isinstance(record.get("chirps", []), list):
            raise ValueError("Invalid WAV annotation record")
        records[name] = record
        duration, rate = wav_header(_within(dataset, name))
        curves = record.get("chirps", [])
        conflict = record.get("review_status") == "no_chirp" and any(
            not isinstance(curve, dict) or curve.get("source") == "manual"
            or curve.get("status") == "validated" or curve.get("edited") for curve in curves)
        positives = [curve for curve in curves if isinstance(curve, dict)
                     and curve.get("source") == "manual" and curve.get("status") == "validated"
                     and not (isinstance(curve.get("annotation_quality"), dict)
                              and curve["annotation_quality"].get("status") == "excluded")]
        label = ("ambiguous" if conflict else "reviewed_no_chirp"
                 if record.get("review_status") == "no_chirp" else "positive_partial"
                 if positives else "unknown")
        wavs.append({"file": name, "session": row["session"], "cohort": row["cohort"],
                     "reference_class": label, "duration_seconds": duration,
                     "sample_rate": rate, "positive_references": len(positives),
                     "manual_pending": sum(isinstance(curve, dict) and curve.get("source") == "manual"
                                           and curve.get("status") != "validated" for curve in curves),
                     "excluded_manual": sum(isinstance(curve, dict) and curve.get("source") == "manual"
                        and curve.get("status") == "validated" and isinstance(curve.get("annotation_quality"), dict)
                        and curve["annotation_quality"].get("status") == "excluded" for curve in curves)})
        if conflict:
            findings.append({"file": name, "issue": "contradictory_no_chirp_label"})
        if duration is None:
            findings.append({"file": name, "issue": "unreadable_or_unsupported_wav_header"})
        identifiers = Counter(str(curve.get("chirp_id")) for curve in positives)
        for curve in positives:
            quality = inspect_chirp(curve, duration, rate)
            flags = quality["flags"].split(";") if quality["flags"] else []
            if curve.get("chirp_id") is None or identifiers[str(curve.get("chirp_id"))] > 1:
                flags.append("missing_or_duplicate_chirp_id")
            chirps.append({**quality, "file": name, "session": row["session"],
                           "cohort": row["cohort"], "flags": ";".join(flags)})
            if flags:
                findings.append({"file": name, "chirp_id": curve.get("chirp_id"), "issue": ";".join(flags)})
    return wavs, chirps, findings, records


def import_benchmark(path: Path | None, manifest_hash: str, assignments, records, verified_negatives):
    if path is None:
        return {}, None, {}
    report = json.loads(path.read_text(encoding="utf-8"))
    metadata = report["metadata"]
    provenance = metadata.get("frozen_dataset") or {}
    if (metadata.get("status") != "complete" or metadata.get("scoring_protocol") != "square_normals_v1"
            or provenance.get("manifest_sha256") != manifest_hash
            or provenance.get("partition") != "calibration" or provenance.get("hashes_verified") is not True):
        raise ValueError("Benchmark provenance or scoring protocol is incompatible")
    rows = {row["dataset_wav"]: row for row in assignments if row["partition"] == "calibration"}
    filtered, _ = remove_excluded_references(records)
    details = {}

    def check_inputs(name):
        if name not in rows:
            raise ValueError("Benchmark contains non-calibration or unknown WAV")
        hashes = metadata.get("input_fingerprints", {}).get(name, {})
        if (hashes.get("wav_sha256") != rows[name]["wav_sha256"]
                or hashes.get("annotation_sha256") != stable_hash(filtered[name])):
            raise ValueError("Benchmark input fingerprints disagree")

    for detail in report["details"]:
        name = detail["file"]
        check_inputs(name)
        key = (name, str(detail["chirp_id"]))
        if key in details:
            raise ValueError("Duplicate benchmark chirp identity")
        value = detail.get("snr_max_db")
        if value is not None and (type(value) not in (int, float) or not math.isfinite(value)):
            raise ValueError("Invalid benchmark SNR")
        details[key] = detail
    negative_measurements = {}
    for row in report.get("per_file", []):
        name = row["file"]
        check_inputs(name)
        if row.get("reference_class") == "reviewed_no_chirp" and name not in verified_negatives:
            raise ValueError("Benchmark negative label is not consistent with current annotations")
        if name in verified_negatives and metadata.get("include_reviewed_negatives") is True:
            count = row.get("candidates")
            if name in negative_measurements or type(count) is not int or count < 0 or "error" in row:
                raise ValueError("Invalid or duplicate negative candidate count")
            negative_measurements[name] = count
    return details, {"sha256": sha256_file(path), "code_fingerprint": metadata.get("code_fingerprint"),
                     "role": "retrospective_descriptive_only", "scoring_protocol": metadata["scoring_protocol"]}, negative_measurements


def audit_cohorts(dataset: Path, output: Path, *, protocol_path=DEFAULT_PROTOCOL,
                  benchmark_report: Path | None = None) -> dict:
    dataset, output = Path(dataset).resolve(), Path(output).resolve()
    if output == dataset or output.is_relative_to(dataset):
        raise ValueError("Output must be outside dataset")
    if output.exists():
        raise ValueError("Use a new output directory; snapshots are never overwritten")
    protocol = read_protocol(protocol_path)
    manifest_hash = sha256_file(dataset / "manifest.csv")
    assignments = assign_cohorts(read_manifest(dataset), protocol)
    wavs, chirps, findings, records = inspect_calibration(dataset, assignments)
    verified_negatives = {row["file"] for row in wavs if row["reference_class"] == "reviewed_no_chirp"}
    details, benchmark, negative_measurements = import_benchmark(
        benchmark_report, manifest_hash, assignments, records, verified_negatives)
    valid_keys = {(row["file"], str(row["chirp_id"])) for row in chirps
                  if "missing_or_duplicate_chirp_id" not in row["flags"]}
    if set(details) - valid_keys:
        raise ValueError("Benchmark refers to a non-reference chirp")
    cohorts = {}
    for cohort in COHORTS:
        members = [row for row in assignments if row["cohort"] == cohort]
        inspected = [row for row in wavs if row["cohort"] == cohort]
        references = [row for row in chirps if row["cohort"] == cohort]
        measured = [details[(row["file"], str(row["chirp_id"]))] for row in references
                    if (row["file"], str(row["chirp_id"])) in details]
        labels = Counter(row["reference_class"] for row in inspected)
        summary = {"sessions": len({row["session"] for row in members}),
                   "session_groups": len({row["session_group"] for row in members}),
                   "manifest_wavs": len(members), "inspected_wavs": len(inspected),
                   "reference_classes": dict(labels), "positive_references": len(references),
                   "wav_duration_seconds": describe(row["duration_seconds"] for row in inspected),
                   "chirp_duration_ms": describe(row["duration_ms"] for row in references),
                   "chirp_min_frequency_khz": describe(row["f_min_khz"] for row in references),
                   "chirp_max_frequency_khz": describe(row["f_max_khz"] for row in references),
                   "flagged_references": sum(bool(row["flags"]) for row in references),
                   "snr_measured": sum(row.get("snr_max_db") is not None for row in measured),
                   "snr_missing": len(references) - sum(row.get("snr_max_db") is not None for row in measured),
                   "existing_metrics_by_snr": summarize_by_snr(measured)}
        if cohort == "final_test_reserved":
            summary = {"sessions": summary["sessions"], "session_groups": summary["session_groups"],
                       "manifest_wavs": len(members), "inspected_wavs": 0,
                       "quality": "not_inspected", "independence": "requires_human_attestation"}
        else:
            summary["snr_distribution"] = {
                name: group["annotated"] for name, group in summary["existing_metrics_by_snr"].items()}
            summary["wav_duration_missing"] = sum(row["duration_seconds"] is None for row in inspected)
            summary["total_recording_seconds_known"] = sum(row["duration_seconds"] or 0 for row in inspected)
            negatives = [row for row in inspected if row["file"] in negative_measurements]
            alarm_count = sum(negative_measurements[row["file"]] for row in negatives)
            alarm_wavs = sum(negative_measurements[row["file"]] > 0 for row in negatives)
            duration_known = negatives and all(row["duration_seconds"] is not None for row in negatives)
            negative_seconds = sum(row["duration_seconds"] or 0 for row in negatives) if duration_known else None
            summary["negative_evaluation"] = {
                "scope": "detector_candidates_only_not_final_tracks",
                "evaluated_wavs": len(negatives), "missing_wavs": labels["reviewed_no_chirp"] - len(negatives),
                "candidate_alerts": alarm_count if negatives else None,
                "wav_false_alarm_fraction": alarm_wavs / len(negatives) if negatives else None,
                "candidate_alerts_per_hour": 3600 * alarm_count / negative_seconds if negative_seconds else None}
            summary["negative_duration_seconds"] = sum(row["duration_seconds"] or 0 for row in inspected
                                                       if row["reference_class"] == "reviewed_no_chirp")
            summary["negative_duration_missing"] = sum(
                row["duration_seconds"] is None for row in inspected if row["reference_class"] == "reviewed_no_chirp")
            summary["limitations"] = ["Retrospective calibration split; independence unverified"]
            if labels["reviewed_no_chirp"] == 0:
                summary["limitations"].append("No verified negative WAVs")
            if summary["snr_missing"]:
                summary["limitations"].append("Incomplete SNR coverage")
            if summary["snr_measured"] and summary["snr_distribution"]["low_15_db_or_less"] == 0:
                summary["limitations"].append("No references at SNR <=15 dB")
            if not references:
                summary["limitations"].append("No positive references")
            if summary["session_groups"] < 2:
                summary["limitations"].append("Single independent session group")
        cohorts[cohort] = summary
    sessions = {}
    for session in sorted({row["session"] for row in wavs}):
        members = [row for row in wavs if row["session"] == session]
        references = [row for row in chirps if row["session"] == session]
        measured = [details[(row["file"], str(row["chirp_id"]))] for row in references
                    if (row["file"], str(row["chirp_id"])) in details]
        sessions[session] = {
            "cohort": members[0]["cohort"], "wavs": len(members),
            "reference_classes": dict(Counter(row["reference_class"] for row in members)),
            "positive_references": len(references),
            "wav_duration_seconds": describe(row["duration_seconds"] for row in members),
            "chirp_duration_ms": describe(row["duration_ms"] for row in references),
            "chirp_min_frequency_khz": describe(row["f_min_khz"] for row in references),
            "chirp_max_frequency_khz": describe(row["f_max_khz"] for row in references),
            "snr_distribution": {name: group["annotated"] for name, group in summarize_by_snr(measured).items()},
            "snr_missing": len(references) - sum(row.get("snr_max_db") is not None for row in measured),
        }
    snapshot = {"schema": "chirp_cohort_snapshot_v1", "protocol": protocol,
                "protocol_sha256": stable_hash(protocol), "manifest_sha256": manifest_hash,
                "assignments": assignments}
    snapshot["snapshot_id"] = stable_hash(snapshot)
    report = {"schema": "chirp_cohort_quality_v1", "snapshot_id": snapshot["snapshot_id"],
              "status": "draft_not_authorized_for_model_selection", "cohorts": cohorts, "sessions": sessions,
              "annotation_hashes_verified": True, "wav_content_hashes_verified": False,
              "negative_verification": "structural_label_consistency_not_acoustic_re_review",
              "test_files_opened": False, "benchmark": benchmark, "findings": findings,
              "implementation_fingerprint": implementation_fingerprint({
                  "laboratory": (ROOT, ("laboratory", "chirp_geometry"))})}
    report["finding_counts"] = dict(Counter(
        issue for finding in findings for issue in finding["issue"].split(";")))
    output.mkdir(parents=True)
    for name, value in (("cohort_snapshot.json", snapshot), ("cohort_quality.json", report)):
        (output / name).write_text(json.dumps(value, ensure_ascii=False, indent=2, allow_nan=False) + "\n", encoding="utf-8")
    for name, rows in (("cohort_assignments.csv", assignments), ("wav_quality.csv", wavs),
                       ("chirp_quality.csv", chirps)):
        with (output / name).open("w", encoding="utf-8-sig", newline="") as stream:
            writer = csv.DictWriter(stream, fieldnames=list(rows[0]) if rows else ["file"])
            writer.writeheader()
            writer.writerows(rows)
    lines = ["# Cohortes scientifiques V1 — proposition", "", f"Snapshot : {snapshot['snapshot_id']}",
             "", "Statut : non approuve pour selection de modele.", "",
             "| Cohorte | Sessions | Groupes | WAV | References | Negatifs | SNR connu |",
             "|---|---:|---:|---:|---:|---:|---:|"]
    for name, summary in cohorts.items():
        lines.append(f"| {name} | {summary['sessions']} | {summary['session_groups']} | "
                     f"{summary['manifest_wavs']} | {summary.get('positive_references', 'non inspecte')} | "
                     f"{summary.get('reference_classes', {}).get('reviewed_no_chirp', 'non inspecte')} | "
                     f"{summary.get('snr_measured', 'non inspecte')} |")
    lines.extend(["", "Les JSON de calibration sont verifies ; les WAV sont lus uniquement par leur en-tete.",
                  "Le test final reste ferme. L'historique d'exposition doit etre atteste humainement.",
                  "Les etiquettes inconnues ne sont jamais des negatifs. Les alertes no_chirp concernent les candidats.",
                  "Les distributions, alertes de qualite et limites par cohorte figurent dans cohort_quality.json."])
    for name in ("development", "validation"):
        summary = cohorts[name]
        lines.extend(["", f"## {name}", "",
            f"WAV inconnus : {summary['reference_classes'].get('unknown', 0)} ; "
            f"etiquettes ambigues : {summary['reference_classes'].get('ambiguous', 0)} ; "
            f"references signalees pour revue : {summary['flagged_references']}.",
            f"SNR (<=15 / 15-35 / >=35 dB) : "
            f"{list(summary['snr_distribution'].values())} ; manquants : {summary['snr_missing']}.",
            f"Durees des chirps (ms) : {summary['chirp_duration_ms']}.",
            f"Frequences minimales (kHz) : {summary['chirp_min_frequency_khz']}.",
            f"Frequences maximales (kHz) : {summary['chirp_max_frequency_khz']}.",
            f"Exposition negative connue (s) : {summary['negative_duration_seconds']} ; "
            f"durees negatives manquantes : {summary['negative_duration_missing']}.",
            f"Evaluation historique des candidats negatifs : {summary['negative_evaluation']}.",
            "", *[f"- {limitation}" for limitation in summary["limitations"]]])
    lines.extend(["", "## Revue et arbitrages", "",
                  f"Motifs de revue : {report['finding_counts']}.",
                  "Un motif de revue ne constitue pas une exclusion automatique.",
                  "Valider le decoupage, l'independance des sessions, les minima de couverture et le protocole final."])
    (output / "cohort_quality.md").write_text("\n".join(lines) + "\n", encoding="utf-8")
    return report


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--dataset", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--protocol", type=Path, default=DEFAULT_PROTOCOL)
    parser.add_argument("--benchmark-report", type=Path)
    args = parser.parse_args(argv)
    try:
        report = audit_cohorts(args.dataset, args.output, protocol_path=args.protocol,
                               benchmark_report=args.benchmark_report)
    except (OSError, ValueError, KeyError, TypeError) as exc:
        parser.exit(2, f"Error: {exc}\n")
    print(json.dumps({"status": report["status"], "cohorts": report["cohorts"]}, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
