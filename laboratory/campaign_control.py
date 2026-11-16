"""Bounded detector campaigns behind the existing local_campaign CLI."""

from __future__ import annotations

import argparse
from contextlib import contextmanager
import csv
from datetime import datetime, timezone
import html
import io
import json
import math
from pathlib import Path
import sys
import time
import uuid

from . import runner
from .autonomous_optimizer import DEFAULTS, judge, metrics
from .dataset import fingerprint_inputs, sha256_file, stable_hash
from .frozen_dataset import collect_frozen_dataset
from .local_campaign import connect_db
from .windows_resources import WindowsJob, campaign_lock, run_bounded

SCHEMA = "local_detector_campaign_v1"
WARNINGS = [
    "Protocole V1 exploratoire : aucune validation independante revendiquee.",
    "Couverture faible SNR insuffisante ; validation historique limitee a une session.",
    "Positifs partiels : les candidats non apparies ne sont pas des faux positifs.",
    "Seuls les WAV no_chirp coherents servent de negatifs ; pas de promotion automatique.",
]


def utc_now():
    return datetime.now(timezone.utc).isoformat()


@contextmanager
def database(folder):
    connection = connect_db(folder / "campaign.sqlite")
    connection.executescript("""
        CREATE TABLE IF NOT EXISTS detector_definition (
            singleton INTEGER PRIMARY KEY CHECK(singleton=1), fingerprint TEXT NOT NULL);
        CREATE TABLE IF NOT EXISTS detector_control (
            singleton INTEGER PRIMARY KEY CHECK(singleton=1), status TEXT NOT NULL, error TEXT);
        CREATE TABLE IF NOT EXISTS detector_jobs (
            job_id TEXT PRIMARY KEY, ordinal INTEGER UNIQUE NOT NULL, name TEXT NOT NULL,
            options_json TEXT NOT NULL, status TEXT NOT NULL, report_sha256 TEXT,
            result_json TEXT, error TEXT);
        CREATE TABLE IF NOT EXISTS detector_attempts (
            attempt_id TEXT PRIMARY KEY, job_id TEXT NOT NULL, started_utc TEXT NOT NULL,
            finished_utc TEXT, reserved_s REAL NOT NULL, elapsed_s REAL,
            status TEXT NOT NULL, outcome_json TEXT, log_path TEXT NOT NULL);
    """)
    try:
        with connection:
            yield connection
    finally:
        connection.close()


def input_identity(dataset, selection_file=None):
    records, provenance = collect_frozen_dataset(dataset, **(
        {"selection_file": selection_file} if selection_file is not None else {}))
    for record in records.values():
        if record.get("review_status") == "no_chirp" and any(
            not isinstance(curve, dict) or curve.get("source") == "manual"
            or curve.get("status") == "validated" or curve.get("edited")
            for curve in record.get("chirps", [])):
            raise ValueError("Contradictory no_chirp label")
    records, _ = runner.remove_excluded_references(records)
    positives = {name for name, record in records.items() if any(
        isinstance(curve, dict) and curve.get("source") == "manual"
        and curve.get("status") == "validated" for curve in record.get("chirps", []))}
    if not positives:
        raise ValueError("No validated positive references")
    selected = {name: record for name, record in records.items()
                if name in positives or record.get("review_status") == "no_chirp"}
    return {"provenance": provenance, "inputs": fingerprint_inputs(selected, dataset)}


def options_with(overrides):
    if not isinstance(overrides, dict) or set(overrides) - set(DEFAULTS):
        raise ValueError("Only existing detector parameters may vary; scoring and reference selection are fixed")
    for name, value in overrides.items():
        if value is None:
            continue
        if name == "trim_echo_rebound":
            if type(value) is not bool:
                raise ValueError("trim_echo_rebound must be boolean")
        elif type(value) not in (int, float) or not math.isfinite(value) or value <= 0:
            raise ValueError(f"Invalid positive parameter: {name}")
        elif name in {"n_fft", "hop"} and type(value) is not int:
            raise ValueError(f"Integer required: {name}")
    return {**runner.DEFAULT_OPTIONS, **overrides}


def prepare(folder, dataset, processing_root, *, variants=(), baseline=None, workers=4,
            experiment_seconds=600, campaign_seconds=3600, memory_gb=16,
            min_free_gb=1, max_experiments=12, max_attempts=3, max_output_gb=None, selection_file=None, global_memory=False,
            diagnostic_probe=False):
    folder, dataset, processing_root = (Path(path).resolve() for path in (folder, dataset, processing_root))
    if folder.exists():
        raise ValueError("Campaign directory already exists; use status/resume, never overwrite")
    for protected in (dataset, processing_root):
        if folder == protected or folder.is_relative_to(protected) or protected.is_relative_to(folder):
            raise ValueError("Campaign output must not overlap corpus or processing sources")
    if folder == runner.ROOT or (folder.is_relative_to(runner.ROOT)
                                and not folder.is_relative_to(runner.ROOT / "laboratory-results")):
        raise ValueError("Inside the repository, use laboratory-results only")
    if max_output_gb is not None:
        raise ValueError("A strict disk quota cannot be guaranteed without an OS-managed quota; use the explicit min-free guard")
    if not 1 <= workers <= 8 or max_experiments < 1 or max_attempts < 1:
        raise ValueError("workers must be 1..8; experiment and attempt budgets must be positive")
    for name, value in (("experiment_seconds", experiment_seconds), ("campaign_seconds", campaign_seconds),
                        ("memory_gb", memory_gb), ("min_free_gb", min_free_gb)):
        if not math.isfinite(value) or value < 0 or (value == 0 and name != "min_free_gb"):
            raise ValueError(f"Invalid resource budget: {name}")
    limits = dict(workers=workers, experiment_seconds=experiment_seconds,
                  campaign_seconds=campaign_seconds, memory_bytes=int(memory_gb * 1024**3),
                  min_free_bytes=int(min_free_gb * 1024**3), max_experiments=max_experiments,
                  max_attempts=max_attempts, disk_policy="monitored_free_space_not_hard_quota")
    with WindowsJob(limits["memory_bytes"]):
        pass
    plans = [{"name": "baseline", "options": options_with(baseline or {})}]
    for variant in variants:
        if not isinstance(variant, dict) or set(variant) != {"name", "options"}:
            raise ValueError("Each variant needs exactly name and options")
        if not isinstance(variant["name"], str) or not variant["name"].strip():
            raise ValueError("Variant names must be nonempty strings")
        plans.append({"name": variant["name"], "options": options_with(variant["options"])})
    if len(plans) > max_experiments:
        raise ValueError("Experiment budget exceeded (baseline included)")
    if len({stable_hash(plan["options"]) for plan in plans}) != len(plans):
        raise ValueError("Duplicate parameter sets are not independent experiments")
    if len({plan["name"] for plan in plans}) != len(plans):
        raise ValueError("Duplicate experiment names")
    identity = input_identity(dataset, selection_file)
    protocol_path = runner.ROOT / "experiments" / "scientific_protocol_v1.json"
    definition = {"schema": SCHEMA, "created_utc": utc_now(), "dataset": str(dataset),
                  "processing_root": str(processing_root), "identity": identity,
                  "code_fingerprint": runner.code_fingerprint(processing_root),
                  "laboratory_git": runner.git_info(runner.ROOT),
                  "processing_git": runner.git_info(processing_root),
                  "scientific_protocol_sha256": sha256_file(protocol_path),
                  "limits": limits, "warnings": WARNINGS, "experiments": plans,
                  "partition": "calibration", "test_access": False, "automatic_promotion": False}
    if selection_file is not None:
        definition["selection_file"] = str(Path(selection_file).resolve())
    if global_memory:
        definition["global_memory"] = True
    if diagnostic_probe:
        if selection_file is None or not global_memory or variants:
            raise ValueError("Diagnostic probe requires a development selection, global budgets and baseline only")
        definition["diagnostic_probe"] = True
    for ordinal, plan in enumerate(plans):
        plan["job_id"] = f"{ordinal:03d}_{stable_hash(plan['options'])[:12]}"
    definition["baseline_identity"] = stable_hash({"code": definition["code_fingerprint"],
        "inputs": identity["inputs"], "options": plans[0]["options"]})
    definition["fingerprint"] = stable_hash(definition)
    folder.mkdir(parents=True)
    runner._write_json_atomic(folder / "definition.json", definition)
    with database(folder) as connection:
        connection.execute("INSERT INTO detector_definition VALUES(1,?)", (definition["fingerprint"],))
        connection.execute("INSERT INTO detector_control VALUES(1,'prepared',NULL)")
        for ordinal, plan in enumerate(plans):
            connection.execute("INSERT INTO detector_jobs(job_id,ordinal,name,options_json,status) VALUES(?,?,?,?, 'planned')",
                (plan["job_id"], ordinal, plan["name"], json.dumps(plan["options"], sort_keys=True)))
    return export_reports(folder)


def load_definition(folder):
    definition = json.loads((folder / "definition.json").read_text(encoding="utf-8"))
    payload = {key: value for key, value in definition.items() if key != "fingerprint"}
    if definition.get("schema") != SCHEMA or stable_hash(payload) != definition.get("fingerprint"):
        raise ValueError("Campaign definition integrity failure")
    with database(folder) as connection:
        saved = connection.execute("SELECT fingerprint FROM detector_definition WHERE singleton=1").fetchone()
    if saved is None or saved[0] != definition["fingerprint"]:
        raise ValueError("Definition and SQLite identity disagree")
    return definition


def verify_environment(definition):
    if runner.code_fingerprint(Path(definition["processing_root"])) != definition["code_fingerprint"]:
        raise ValueError("Code or environment changed; prepare a new campaign")
    protocol = runner.ROOT / "experiments" / "scientific_protocol_v1.json"
    if sha256_file(protocol) != definition["scientific_protocol_sha256"]:
        raise ValueError("Scientific protocol changed; prepare a new campaign")
    if input_identity(Path(definition["dataset"]), definition.get("selection_file")) != definition["identity"]:
        raise ValueError("Corpus content or manifest changed; prepare a new campaign")


def benchmark_path(folder, job_id):
    return folder / "jobs" / job_id / "benchmark" / "report.json"


def validated_report(path, definition, options):
    report = json.loads(path.read_text(encoding="utf-8"))
    metadata = report["metadata"]
    if (metadata.get("status") != "complete" or metadata.get("code_fingerprint") != definition["code_fingerprint"]
            or bool(metadata.get("diagnostic_probe")) != bool(definition.get("diagnostic_probe"))
            or metadata.get("options") != options or metadata.get("input_fingerprints") != definition["identity"]["inputs"]
            or (metadata.get("frozen_dataset") or {}).get("partition") != "calibration"
            or (metadata.get("frozen_dataset") or {}).get("hashes_verified") is not True
            or (metadata.get("frozen_dataset") or {}).get("manifest_sha256") != definition["identity"]["provenance"]["manifest_sha256"]):
        raise ValueError("Incomplete or incompatible benchmark result")
    rows = report["per_file"]
    if (len(rows) != len(definition["identity"]["inputs"])
            or {row.get("file") for row in rows} != set(definition["identity"]["inputs"])
            or any("error" in row for row in rows)):
        raise ValueError("Benchmark does not cover exactly the selected inputs")
    if definition.get("diagnostic_probe"):
        diagnostics = report.get("diagnostics", {})
        if set(diagnostics) != set(definition["identity"]["inputs"]):
            raise ValueError("Diagnostic traces do not cover the selected inputs")
        for diagnostic in diagnostics.values():
            proof = diagnostic.get("equivalence", {})
            if not all(proof.get(key) is True for key in (
                    "benchmark_numeric_and_decisions_exact", "candidate_arrays_exact", "matched_trajectory_arrays_exact")):
                raise ValueError("Instrumentation non-regression proof is missing")
            if diagnostic.get("negative") is not None and proof.get("negative_final_arrays_exact") is not True:
                raise ValueError("Negative output equivalence proof is missing")
    return report


def attempt(folder, connection, definition, job_id):
    limits = definition["limits"]
    charged = connection.execute("SELECT COALESCE(SUM(COALESCE(elapsed_s,reserved_s)),0) FROM detector_attempts").fetchone()[0]
    remaining = limits["campaign_seconds"] - charged
    if remaining <= 0:
        raise RuntimeError("Cumulative execution-time budget exhausted, including crash reservations")
    reservation = min(remaining, limits["experiment_seconds"])
    attempt_id = uuid.uuid4().hex
    directory = folder / "attempts" / attempt_id
    directory.mkdir(parents=True)
    log = directory / "execution.log"
    connection.execute("INSERT INTO detector_attempts(attempt_id,job_id,started_utc,reserved_s,status,log_path) VALUES(?,?,?,?, 'running',?)",
                       (attempt_id, job_id, utc_now(), reservation, str(log.relative_to(folder))))
    connection.commit()
    command = [sys.executable, "-m", "laboratory.campaign_control", "_execute",
               "--campaign", str(folder), "--job-id", job_id, "--attempt", attempt_id]
    started = time.monotonic()
    try:
        outcome = run_bounded(command, folder=folder, gate=directory / "start.gate", log=log,
            seconds=reservation, memory_bytes=limits["memory_bytes"], min_free_bytes=limits["min_free_bytes"],
            stop_requested=lambda: (folder / "STOP.json").exists())
        if outcome["status"] == "complete":
            receipt = json.loads((directory / "receipt.json").read_text(encoding="utf-8"))
            if receipt.get("attempt_id") != attempt_id or receipt.get("definition") != definition["fingerprint"]:
                raise ValueError("Worker receipt integrity failure")
            if job_id != "__verify__":
                path = benchmark_path(folder, job_id)
                if receipt.get("report_sha256") != sha256_file(path):
                    raise ValueError("Worker report does not match receipt")
                plan = next(plan for plan in definition["experiments"] if plan["job_id"] == job_id)
                metrics(validated_report(path, definition, plan["options"]))
    except Exception as exc:
        outcome = {"status": "failed", "error": f"{type(exc).__name__}: {exc}",
                   "elapsed_s": time.monotonic() - started}
    connection.execute("UPDATE detector_attempts SET finished_utc=?,elapsed_s=?,status=?,outcome_json=? WHERE attempt_id=?",
                       (utc_now(), outcome["elapsed_s"], outcome["status"], json.dumps(outcome), attempt_id))
    connection.commit()
    return outcome


def run_campaign(folder, *, resume=False, max_new=None, retry_failed=False):
    folder = Path(folder).resolve()
    definition = load_definition(folder)
    if max_new is not None and max_new < 1:
        raise ValueError("max-new must be positive")
    with campaign_lock(folder), database(folder) as connection:
        connection.execute("UPDATE detector_attempts SET status='interrupted' WHERE status='running'")
        connection.execute("UPDATE detector_jobs SET status='interrupted',error='Previous supervisor stopped' WHERE status='running'")
        if resume:
            (folder / "STOP.json").unlink(missing_ok=True)
        connection.execute("UPDATE detector_control SET status='running',error=NULL")
        connection.commit()
        state, error = "failed", None
        try:
            check = attempt(folder, connection, definition, "__verify__")
            if check["status"] != "complete":
                state, error = check["status"], check.get("error")
            else:
                state = "complete"
                fresh = 0
                baseline_metrics = None
                for ordinal, plan in enumerate(definition["experiments"]):
                    job_id = plan["job_id"]
                    saved = connection.execute("SELECT status,report_sha256 FROM detector_jobs WHERE job_id=?", (job_id,)).fetchone()
                    path = benchmark_path(folder, job_id)
                    if saved[0] == "complete":
                        if not path.is_file() or sha256_file(path) != saved[1]:
                            if ordinal == 0:
                                raise ValueError("Fixed baseline report changed; refusing to replace it")
                            saved = ("invalid", None)
                        else:
                            report = validated_report(path, definition, plan["options"])
                            if ordinal == 0:
                                baseline_metrics = metrics(report)
                            continue
                    if (folder / "STOP.json").exists():
                        state, error = "interrupted", "Stop requested"
                        break
                    if max_new is not None and fresh >= max_new:
                        state = "paused"
                        break
                    if saved[0] in {"failed", "timed_out", "disk_guard", "blocked"} and not retry_failed:
                        state = "failed"
                        if ordinal == 0:
                            break
                        continue
                    count = connection.execute("SELECT COUNT(*) FROM detector_attempts WHERE job_id=?", (job_id,)).fetchone()[0]
                    if count >= definition["limits"]["max_attempts"]:
                        connection.execute("UPDATE detector_jobs SET status='blocked',error='Attempt budget exhausted' WHERE job_id=?", (job_id,))
                        state = "failed"
                        if ordinal == 0:
                            break
                        continue
                    connection.execute("UPDATE detector_jobs SET status='running',error=NULL WHERE job_id=?", (job_id,))
                    connection.commit()
                    export_reports(folder)
                    outcome = attempt(folder, connection, definition, job_id)
                    fresh += 1
                    result = None
                    if outcome["status"] == "complete":
                        try:
                            report = validated_report(path, definition, plan["options"])
                            measure = metrics(report)
                            if ordinal == 0:
                                baseline_metrics = measure
                            accepted, reason, score = judge(measure, baseline_metrics)
                            result = {"metrics": measure, "diagnostic_gate": accepted, "reason": reason,
                                "diagnostic_score": score, "baseline_identity": definition["baseline_identity"],
                                "deltas": {key: value - baseline_metrics[key] for key, value in measure.items()
                                           if type(value) in (int, float) and type(baseline_metrics.get(key)) in (int, float)},
                                "false_final_trajectories": None, "automatic_promotion": False}
                        except Exception as exc:
                            outcome.update(status="failed", error=f"Invalid result: {exc}")
                    digest = sha256_file(path) if result else None
                    connection.execute("UPDATE detector_jobs SET status=?,report_sha256=?,result_json=?,error=? WHERE job_id=?",
                        (outcome["status"], digest, json.dumps(result) if result else None, outcome.get("error"), job_id))
                    connection.commit()
                    export_reports(folder)
                    if outcome["status"] != "complete":
                        state, error = outcome["status"], outcome.get("error")
                        if ordinal == 0 or state in {"interrupted", "disk_guard", "timed_out"}:
                            break
                if connection.execute("SELECT COUNT(*) FROM detector_jobs WHERE status!='complete'").fetchone()[0] == 0:
                    state = "complete"
        except KeyboardInterrupt:
            state, error = "interrupted", "Supervisor keyboard interrupt"
        except Exception as exc:
            state, error = "failed", f"{type(exc).__name__}: {exc}"
        finally:
            connection.execute("UPDATE detector_control SET status=?,error=?", (state, error))
            connection.commit()
        return export_reports(folder)


def atomic_text(path, content):
    temporary = path.with_name(f".{path.name}.{uuid.uuid4().hex}.tmp")
    temporary.write_text(content, encoding="utf-8")
    temporary.replace(path)


def export_reports(folder):
    folder = Path(folder)
    definition = load_definition(folder)
    with database(folder) as connection:
        control = connection.execute("SELECT status,error FROM detector_control").fetchone()
        jobs = []
        for job_id, name, options, status, digest, result, error in connection.execute(
                "SELECT job_id,name,options_json,status,report_sha256,result_json,error FROM detector_jobs ORDER BY ordinal"):
            jobs.append({"job_id": job_id, "name": name, "parameters": json.loads(options), "status": status,
                         "report_sha256": digest, "result": json.loads(result) if result else None, "error": error})
        attempts = []
        for row in connection.execute("SELECT attempt_id,job_id,started_utc,finished_utc,reserved_s,elapsed_s,status,outcome_json,log_path FROM detector_attempts ORDER BY started_utc"):
            item = dict(zip(("attempt_id", "job_id", "started_utc", "finished_utc", "reserved_s", "elapsed_s", "status", "outcome", "log_path"), row))
            item["outcome"] = json.loads(item["outcome"]) if item["outcome"] else None
            attempts.append(item)
    report = {"schema": SCHEMA, "status": control[0], "error": control[1], "definition": definition["fingerprint"],
              "baseline_identity": definition["baseline_identity"], "baseline_report_sha256": jobs[0]["report_sha256"],
              "progress": {"complete": sum(job["status"] == "complete" for job in jobs), "total": len(jobs)},
              "execution_seconds_charged": sum(item["elapsed_s"] if item["elapsed_s"] is not None else item["reserved_s"] for item in attempts),
              "limits": definition["limits"], "warnings": WARNINGS, "jobs": jobs, "attempts": attempts,
              "final_false_trajectory_metric": None, "automatic_promotion": False, "test_access": False}
    if definition.get("global_memory"):
        from .global_campaign import usage
        report["global_supervision"] = usage(folder)
    for job in jobs:
        path = benchmark_path(folder, job["job_id"])
        if job["status"] == "complete" and (not path.is_file() or sha256_file(path) != job["report_sha256"]):
            job["status"], job["error"] = "integrity_error", "Completed report is missing or changed"
            report["status"] = "integrity_error"
        related = [item for item in attempts if item["job_id"] == job["job_id"]]
        job["attempts"] = len(related)
        job["measured_duration_s"] = sum(item["elapsed_s"] or 0 for item in related)
        job["charged_seconds"] = sum(item["elapsed_s"] if item["elapsed_s"] is not None else item["reserved_s"] for item in related)
        peaks = [(item["outcome"] or {}).get("peak_job_commit_bytes") for item in related]
        job["peak_job_commit_bytes"] = max((value for value in peaks if value is not None), default=None)
    report["progress"]["complete"] = sum(job["status"] == "complete" for job in jobs)
    if report["status"] == "running":
        try:
            with campaign_lock(folder):
                report["status"] = "interrupted_owner_missing"
                report["error"] = "No supervisor owns the lock; resume will recover unfinished attempts"
        except RuntimeError:
            pass
    runner._write_json_atomic(folder / "campaign_report.json", report)
    stream = io.StringIO(newline="")
    writer = csv.DictWriter(stream, fieldnames=("job_id", "name", "status", "parameters", "report_sha256",
        "attempts", "measured_duration_s", "charged_seconds", "peak_job_commit_bytes", "error", "comparison"))
    writer.writeheader()
    for job in jobs:
        writer.writerow({**{key: job[key] for key in ("job_id", "name", "status", "report_sha256",
            "attempts", "measured_duration_s", "charged_seconds", "peak_job_commit_bytes", "error")},
                         "parameters": json.dumps(job["parameters"], sort_keys=True), "comparison": json.dumps(job["result"])})
    atomic_text(folder / "campaign_scores.csv", stream.getvalue())
    rows = ""
    for job in jobs:
        related = [item for item in attempts if item["job_id"] == job["job_id"]]
        duration = job["measured_duration_s"]
        peak = job["peak_job_commit_bytes"]
        result = job["result"] or {}
        measures = result.get("metrics", {})
        comparison = json.dumps(result.get("deltas", {}), ensure_ascii=False, sort_keys=True)
        rows += "<tr>" + "".join(f"<td>{html.escape(str(value))}</td>" for value in (
            job["name"], job["status"], round(duration, 3), round(peak / 1024**2, 2) if peak is not None else None,
            measures.get("recall"), measures.get("negative_candidates"), comparison, job["error"]))
        rows += f"<td><a href='jobs/{job['job_id']}/benchmark/report.html'>Benchmark</a> "
        rows += " ".join(f"<a href='{html.escape(item['log_path'])}'>Log {index+1}</a>" for index, item in enumerate(related))
        rows += "</td></tr>"
    document = "<!doctype html><html lang='fr'><meta charset='utf-8'><title>Campagne locale</title>"
    document += "<style>body{font:15px sans-serif;margin:2em}table{border-collapse:collapse;width:100%;table-layout:fixed}td,th{padding:.5em;overflow-wrap:anywhere;vertical-align:top}th{background:#eee}</style>"
    document += "<h1>Campagne exploratoire locale</h1><p>" + html.escape(str(report["progress"])) + " — " + html.escape(report["status"]) + "</p>"
    document += "<ul>" + "".join(f"<li>{html.escape(warning)}</li>" for warning in WARNINGS) + "</ul>"
    document += f"<p>Baseline : {definition['baseline_identity']}</p><p>Erreur : {html.escape(str(report['error']))}</p>"
    document += "<table border='1'><tr><th>Experience</th><th>Statut</th><th>Secondes</th><th>Pic engage Mio</th><th>Rappel</th><th>Alertes candidates negatives</th><th>Ecarts a la baseline</th><th>Erreur</th><th>Resultats</th></tr>" + rows + "</table>"
    document += "<p>Les fausses trajectoires finales sont indisponibles. Les alertes candidates no_chirp restent distinctes.</p>"
    atomic_text(folder / "campaign_report.html", document + "</html>")
    return report


def execute_worker(folder, job_id, attempt_id):
    directory = folder / "attempts" / attempt_id
    deadline = time.monotonic() + 30
    while not (directory / "start.gate").exists():
        if time.monotonic() >= deadline:
            raise RuntimeError("Supervisor never assigned the Windows Job Object")
        time.sleep(0.05)
    definition = load_definition(folder)
    receipt = {"attempt_id": attempt_id, "definition": definition["fingerprint"]}
    if job_id == "__verify__":
        verify_environment(definition)
    else:
        plan = next(plan for plan in definition["experiments"] if plan["job_id"] == job_id)
        runner.run(dataset=Path(definition["dataset"]), out=benchmark_path(folder, job_id).parent,
                   processing_root=Path(definition["processing_root"]), workers=definition["limits"]["workers"],
                   options=plan["options"], include_reviewed_negatives=True,
                   **({"selection_file": Path(definition["selection_file"])} if definition.get("selection_file") else {}),
                   **({"diagnostic_probe": True} if definition.get("diagnostic_probe") else {}))
        verify_environment(definition)
        receipt["report_sha256"] = sha256_file(benchmark_path(folder, job_id))
    runner._write_json_atomic(directory / "receipt.json", receipt)


def synthetic_dataset(path):
    import numpy as np
    import soundfile as sf

    if path.exists():
        raise ValueError("Synthetic input directory already exists; refusing overwrite")
    wav_folder = path / "calibration" / "wav" / "synthetic"
    annotation = path / "calibration" / "annotations" / "synthetic" / "synthetic.json"
    wav_folder.mkdir(parents=True)
    annotation.parent.mkdir(parents=True)
    rate = 384000
    times = np.arange(19200) / rate
    audio = np.random.default_rng(123).normal(0, 0.0001, len(times))
    mask = (times >= 0.010) & (times <= 0.015)
    relative = times[mask] - 0.010
    audio[mask] += 0.5 * np.sin(2 * np.pi * (80000 * relative - 0.5 * 8000000 * relative**2))
    sf.write(wav_folder / "positive.wav", audio, rate, subtype="PCM_16")
    sf.write(wav_folder / "negative.wav", np.zeros(len(times)), rate, subtype="PCM_16")
    files = {"positive.wav": {"chirps": [{"chirp_id": 1, "source": "manual", "status": "validated",
                "points": [{"t_ms": 10, "f_khz": 80}, {"t_ms": 15, "f_khz": 40}]}]},
             "negative.wav": {"review_status": "no_chirp", "chirps": []}}
    runner._write_json_atomic(annotation, {"schema_version": 1, "created_by": "Bat-Acoustic-Analyser",
        "units": {"time": "ms", "frequency": "kHz"}, "files": files, "synthetic": True})
    with (path / "manifest.csv").open("w", newline="", encoding="utf-8") as stream:
        writer = csv.DictWriter(stream, fieldnames=("partition", "session", "dataset_wav", "dataset_json", "wav_sha256", "json_sha256"))
        writer.writeheader()
        for name in files:
            wav = wav_folder / name
            writer.writerow(dict(partition="calibration", session="synthetic", dataset_wav=wav.relative_to(path).as_posix(),
                                 dataset_json=annotation.relative_to(path).as_posix(), wav_sha256=sha256_file(wav), json_sha256=sha256_file(annotation)))


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    commands = parser.add_subparsers(dest="command", required=True)
    for name in ("prepare", "demo", "start", "resume", "status", "stop", "_execute"):
        command = commands.add_parser(name)
        command.add_argument("--campaign", type=Path, required=True)
        if name in {"prepare", "demo"}:
            command.add_argument("--processing-root", type=Path, default=runner.DEFAULT_PROCESSING_ROOT)
            command.add_argument("--workers", type=int, default=4)
            command.add_argument("--experiment-seconds", type=float, default=60 if name == "demo" else 600)
            command.add_argument("--campaign-seconds", type=float, default=180 if name == "demo" else 3600)
            command.add_argument("--memory-gb", type=float, default=16)
            command.add_argument("--min-free-gb", type=float, default=1)
            command.add_argument("--max-output-gb", type=float)
            command.add_argument("--max-experiments", type=int, default=12)
            command.add_argument("--max-attempts", type=int, default=3)
            command.add_argument("--global-memory", action="store_true")
            command.add_argument("--diagnostic-probe", action="store_true")
        if name == "prepare":
            command.add_argument("--dataset", type=Path, required=True)
            command.add_argument("--variants", type=Path)
            command.add_argument("--baseline-options", type=Path)
            command.add_argument("--selection", type=Path)
        if name in {"start", "resume", "demo"}:
            command.add_argument("--max-new", type=int)
            command.add_argument("--retry-failed", action="store_true")
        if name == "_execute":
            command.add_argument("--job-id", required=True)
            command.add_argument("--attempt", required=True)
        if name == "status":
            command.add_argument("--watch", action="store_true")
    args = parser.parse_args(argv)
    folder = args.campaign.resolve()
    try:
        if args.command == "_execute":
            execute_worker(folder, args.job_id, args.attempt)
            return 0
        if args.command in {"prepare", "demo"}:
            if args.command == "demo":
                dataset = folder.with_name(folder.name + "-synthetic-input")
                if folder.exists():
                    raise ValueError("Demo campaign already exists; use resume")
                synthetic_dataset(dataset)
                variants = [{"name": "candidate-width-1ms", "options": {"v4_general_min_width_ms": 1.0}}]
                baseline = None
            else:
                dataset = args.dataset
                variants = json.loads(args.variants.read_text(encoding="utf-8")) if args.variants else []
                baseline = json.loads(args.baseline_options.read_text(encoding="utf-8")) if args.baseline_options else None
            report = prepare(folder, dataset, args.processing_root, variants=variants, baseline=baseline,
                workers=args.workers, experiment_seconds=args.experiment_seconds, campaign_seconds=args.campaign_seconds,
                memory_gb=args.memory_gb, min_free_gb=args.min_free_gb, max_experiments=args.max_experiments,
                max_attempts=args.max_attempts, max_output_gb=args.max_output_gb,
                selection_file=args.selection if args.command == "prepare" else None,
                global_memory=args.global_memory, diagnostic_probe=args.diagnostic_probe)
        if args.command in {"start", "resume", "demo"}:
            execute = run_campaign
            if load_definition(folder).get("global_memory"):
                from .global_campaign import run_global
                execute = run_global
            report = execute(folder, resume=args.command == "resume", max_new=args.max_new, retry_failed=args.retry_failed)
        elif args.command == "status":
            report = export_reports(folder)
            while args.watch and report["status"] == "running":
                print(json.dumps({"status": report["status"], "progress": report["progress"]}), flush=True)
                time.sleep(1)
                report = export_reports(folder)
        elif args.command == "stop":
            definition = load_definition(folder)
            runner._write_json_atomic(folder / "STOP.json", {"requested_utc": utc_now(), "definition": definition["fingerprint"]})
            print("Stop requested; use status to confirm termination")
            return 0
    except (OSError, ValueError, RuntimeError, KeyError) as exc:
        parser.exit(2, f"Error: {exc}\n")
    print(json.dumps({"status": report["status"], "progress": report["progress"], "error": report["error"],
                      "report": str(folder / "campaign_report.html")}, ensure_ascii=False, indent=2))
    return 0 if report["status"] in {"prepared", "running", "paused", "complete"} else 1


if __name__ == "__main__":
    raise SystemExit(main())
