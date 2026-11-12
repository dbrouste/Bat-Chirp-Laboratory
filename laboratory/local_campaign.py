"""Local, resumable experiment runner for manual-chirp QA calibration.

Runs one full acoustic campaign per *window length*, then evaluates every
threshold offline from the exported scalar metrics: thresholds DO NOT trigger
new STFT/Reassignment work. No cloud, no GitHub Actions, no data uploads.

The reference 'calibration' partition is the only data partition used by the
underlying sensitivity audit. Original WAVs and JSON are read-only.
"""
from __future__ import annotations

import argparse
import csv
import hashlib
import html
import json
import sqlite3
import subprocess
import sys
import time
from collections import defaultdict
from datetime import datetime, timezone
from pathlib import Path

DEFAULT_WINDOWS = (3, 4, 5, 7)
DEFAULT_THRESHOLDS = (0.75, 1.0, 1.5, 2.0, 2.5)
SCHEMA_VERSION = 1


def parse_ints(value):
    result = tuple(int(s.strip()) for s in value.split(",") if s.strip())
    if not result or len(set(result)) != len(result) or any(x < 3 or x > 20 for x in result):
        raise ValueError("windows must be distinct integers in [3,20]")
    return result


def parse_floats(value):
    result = tuple(float(s.strip()) for s in value.split(",") if s.strip())
    if not result or len(set(result)) != len(result) or any(x <= 0 or x > 20 for x in result):
        raise ValueError("thresholds must be distinct numbers in (0,20]")
    return result


def run_key(dataset, *, max_chirps, skip_chirps, fft_size, stft_hop,
            reassignment_hop, spectral_alpha, window, analyser_root=None):
    """Fingerprint the run definition, not sensitive WAV contents."""
    manifest = (dataset / "manifest.csv").read_bytes()
    # Include actual dataset JSON/WAV metadata and implementation code:
    # do not reuse stale results after an annotation or algorithm change.
    with (dataset / "manifest.csv").open(encoding="utf-8-sig", newline="") as stream:
        entries = list(csv.DictReader(stream))
    sources = []
    for entry in entries:
        if entry.get("partition") != "calibration":
            continue
        for field in ("dataset_json", "dataset_wav"):
            item = (dataset / entry[field]).resolve()
            if not item.is_relative_to(dataset):
                raise ValueError("manifest references path outside dataset")
            stat = item.stat()
            sources.append((str(item.relative_to(dataset)), stat.st_size,
                            stat.st_mtime_ns))
    sources = sorted(set(sources))
    code_root = Path(__file__).resolve().parents[1]
    implementation = []
    for relative in ("laboratory/sensitivity_audit.py",
                     "laboratory/acoustic_audit_v2.py",
                     "laboratory/local_quality.py",
                     "laboratory/transverse_ridge.py",
                     "chirp_geometry.py"):
        item = code_root / relative
        if item.is_file():
            implementation.append((relative,hashlib.sha256(item.read_bytes()).hexdigest()))
    definition = {
        "version": SCHEMA_VERSION, "dataset": str(dataset),
        "manifest_sha256": hashlib.sha256(manifest).hexdigest(),
        "dataset_file_metadata": sources,
        "code_hashes": implementation,
        "max_chirps": max_chirps, "skip_chirps": skip_chirps,
        "fft_size": fft_size, "stft_hop": stft_hop,
        "reassignment_hop": reassignment_hop,
        "spectral_alpha": spectral_alpha, "window": window,
        "analyser_root": str(Path(analyser_root).resolve()) if analyser_root else None,
    }
    return hashlib.sha256(json.dumps(definition, sort_keys=True).encode()).hexdigest()[:20]


def connect_db(path):
    db = sqlite3.connect(str(path))
    db.execute("""CREATE TABLE IF NOT EXISTS experiments (
        run_id TEXT PRIMARY KEY, window_points INTEGER NOT NULL,
        state TEXT NOT NULL, created_at TEXT NOT NULL,
        finished_at TEXT, elapsed_seconds REAL,
        output_dir TEXT NOT NULL, error TEXT
    )""")
    db.execute("""CREATE TABLE IF NOT EXISTS threshold_results (
        run_id TEXT NOT NULL, threshold REAL NOT NULL,
        scenario TEXT NOT NULL, region TEXT NOT NULL,
        total INTEGER NOT NULL, valid INTEGER NOT NULL,
        baseline_flagged INTEGER NOT NULL, new_alerts INTEGER NOT NULL,
        legacy_new_alerts INTEGER NOT NULL,
        PRIMARY KEY (run_id, threshold, scenario, region)
    )""")
    db.commit()
    return db


def truth(value):
    return str(value).strip().lower() == "true"


def scored_rows(csv_path, threshold):
    """Compute *new* flags relative to each baseline at a chosen threshold."""
    groups = defaultdict(lambda: [0, 0, 0, 0, 0])
    with csv_path.open(encoding="utf-8-sig", newline="") as stream:
        for row in csv.DictReader(stream):
            scenario = row["scenario"]
            region = row["region"]
            for key in {(scenario, "all"), (scenario, region)}:
                totals = groups[key]
                totals[0] += 1
                if row["error"]:
                    continue
                totals[1] += 1
                before = float(row["baseline_local_score_eq"])
                after = float(row["perturbed_local_score_eq"])
                old_alert = (row["baseline_priority"] == "no_alert_unverified"
                             and row["perturbed_priority"] != "no_alert_unverified")
                totals[2] += before >= threshold
                totals[3] += before < threshold <= after
                totals[4] += old_alert
    return groups


def persist_thresholds(db, run_id, csv_path, thresholds):
    for threshold in thresholds:
        for (scenario, region), counts in scored_rows(csv_path, threshold).items():
            db.execute("""INSERT OR REPLACE INTO threshold_results
                (run_id, threshold, scenario, region, total, valid,
                 baseline_flagged, new_alerts, legacy_new_alerts)
                 VALUES (?,?,?,?,?,?,?,?,?)""",
                       (run_id, threshold, scenario, region, *counts))
    db.commit()


def collect_rows(db, run_ids, thresholds):
    results = []
    for run_id in run_ids:
        for threshold in thresholds:
            data = db.execute("""SELECT e.window_points, r.threshold,
                r.scenario,r.region,r.total,r.valid,r.baseline_flagged,
                r.new_alerts,r.legacy_new_alerts
                FROM threshold_results r JOIN experiments e USING (run_id)
                WHERE r.run_id=? AND r.threshold=? ORDER BY r.scenario,r.region""",
                (run_id,threshold)).fetchall()
            for values in data:
                results.append(dict(zip(
                    ("window","threshold","scenario","region","total",
                     "valid","baseline_flagged","new_alerts","legacy_new_alerts"),
                    values)))
    return results


def save_reports(output, records, runs, definition):
    all_rows = [r for r in records if r["region"]=="all"]
    # By construction, each chirp contributes multiple synthetic variants.
    # A baseline count here is the number of *variants* whose shared original
    # chirp was already flagged, NOT a count of distinct chirps.
    summary = {
        "version":"local_campaign_v1",
        "definition":definition,
        "runs":runs,
        "note":("Threshold sweeps are calculated from precomputed window scores. "
                "Baseline flags are counts of variants, not unique chirps. "
                "Reference annotations are not necessarily correct. "
                "Do not interpret new-alert rate as precision or recall; "
                "no automatic winner is selected."),
        "results":all_rows,
    }
    (output/"campaign_summary.json").write_text(
        json.dumps(summary, ensure_ascii=False, indent=2)+"\n",encoding="utf-8")
    with (output/"campaign_scores.csv").open("w",encoding="utf-8-sig",newline="") as stream:
        columns=("window","threshold","scenario","region","total","valid",
                 "baseline_flagged","new_alerts","legacy_new_alerts")
        writer=csv.DictWriter(stream,fieldnames=columns)
        writer.writeheader()
        writer.writerows(records)

    table = []
    for row in all_rows:
        valid=row["valid"]
        rate=(100*row["new_alerts"]/valid) if valid else 0
        table.append("<tr>"+ "".join(
            "<td>"+html.escape(str(item))+"</td>" for item in (
                row["window"],row["threshold"],row["scenario"],
                f'{row["new_alerts"]}/{valid} ({rate:.1f}%)',
                row["legacy_new_alerts"],row["baseline_flagged"]))+"</tr>")
    rows_html="\n".join(table)
    document = """<!doctype html><html lang="fr"><head><meta charset="utf-8">
<title>Bat-Chirp-Laboratory — Campagne locale</title>
<style>body{font:15px system-ui,sans-serif;max-width:1100px;margin:auto;padding:30px;
color:#1b2832}h1{margin-bottom:.2em}.note{background:#f2f5f7;padding:16px;
border-left:4px solid #8795a5}table{border-collapse:collapse;width:100%}
td,th{padding:9px;border-bottom:1px solid #ddd;text-align:right}
th:nth-child(3),td:nth-child(3){text-align:left}tr:hover{background:#f5f7fa}
small{color:#556}</style></head><body>
<h1>Laboratoire local — comparaison automatique</h1>
<p>Balayage de la longueur des fenêtres et des seuils, sans recalcul pour chaque seuil.</p>
<p class="note"><strong>Important :</strong> les annotations de référence ne
sont pas toutes validées comme justes. Une nouvelle alerte n'est donc
pas une mesure de précision, de rappel ou de faux positifs.
Le benchmark n'est pas modifié et aucune variante n'est promue automatiquement.</p>
<table><thead><tr><th>Fenêtre</th><th>Seuil (kHz_eq)</th><th>Scénario</th>
<th>Nouvelles alertes</th><th>Anciennes alertes</th><th>Références déjà signalées*
</th></tr></thead><tbody>""" + rows_html + """</tbody></table>
<p><small>* Comptées par perturbation et non par chirp distinct.</small></p>
<p>Le CSV <code>campaign_scores.csv</code> contient aussi la ventilation par
région (début, milieu, fin). L'historique se trouve dans
<code>campaign.sqlite</code>.</p></body></html>"""
    (output/"campaign_report.html").write_text(document,encoding="utf-8")


def run_campaign(dataset, output, *, analyser_root=None, max_chirps=30,
                 skip_chirps=3, windows=DEFAULT_WINDOWS,
                 thresholds=DEFAULT_THRESHOLDS, fft_size=1024,
                 stft_hop=32, reassignment_hop=16, spectral_alpha=0.0,
                 retry_failed=False, max_trials=None):
    dataset=Path(dataset).resolve()
    output=Path(output).resolve()
    if output==dataset or output.is_relative_to(dataset):
        raise ValueError("output must be outside dataset")
    if not (dataset/"manifest.csv").is_file():
        raise FileNotFoundError(f"missing manifest: {dataset/'manifest.csv'}")
    if max_chirps<1 or skip_chirps<0:
        raise ValueError("invalid chirp selection")
    if max_trials is not None and max_trials<1:
        raise ValueError("max_trials must be positive")
    output.mkdir(parents=True,exist_ok=True)
    db=connect_db(output/"campaign.sqlite")
    definition = dict(dataset=str(dataset),max_chirps=max_chirps,
                      skip_chirps=skip_chirps,windows=list(windows),
                      thresholds=list(thresholds))
    runs=[]
    run_ids=[]
    completed=0
    try:
        for window in windows:
            key=run_key(dataset,max_chirps=max_chirps,
                        skip_chirps=skip_chirps,fft_size=fft_size,
                        stft_hop=stft_hop,
                        reassignment_hop=reassignment_hop,
                        spectral_alpha=spectral_alpha,window=window,
                        analyser_root=analyser_root)
            folder=output/"runs"/key
            csv_file=folder/"sensitivity_cases.csv"
            existing=db.execute("SELECT state,output_dir FROM experiments WHERE run_id=?",
                                (key,)).fetchone()
            if existing and existing[0]=="completed" and csv_file.is_file():
                state="completed_cached"
            elif existing and existing[0]=="failed" and not retry_failed:
                state="failed_cached"
            elif max_trials is not None and completed>=max_trials:
                state="budget_exhausted"
            else:
                folder.mkdir(parents=True,exist_ok=True)
                command=[
                    sys.executable,"-m","laboratory.sensitivity_audit",
                    "--dataset",str(dataset),"--output",str(folder),
                    "--max-chirps",str(max_chirps),"--skip-chirps",str(skip_chirps),
                    "--fft-size",str(fft_size),
                    "--stft-hop",str(stft_hop),
                    "--reassignment-hop",str(reassignment_hop),
                    "--spectral-alpha",str(spectral_alpha),
                    "--local-window",str(window),
                    "--local-threshold","1.5",
                ]
                if analyser_root is not None:
                    command.extend(["--analyser-root",str(Path(analyser_root).resolve())])
                started=time.perf_counter()
                now=datetime.now(timezone.utc).isoformat()
                db.execute("""INSERT OR REPLACE INTO experiments
                    (run_id,window_points,state,created_at,finished_at,
                     elapsed_seconds,output_dir,error)
                     VALUES (?,?,?,?,?,?,?,?)""",
                    (key,window,"running",now,None,None,str(folder),None))
                db.commit()
                with (folder/"execution.log").open("w",encoding="utf-8") as log:
                    # No shell: command arguments cannot alter other files.
                    proc=subprocess.run(command,cwd=str(Path(__file__).resolve().parents[1]),
                                        stdout=log,stderr=subprocess.STDOUT,
                                        check=False)
                elapsed=round(time.perf_counter()-started,3)
                state="completed" if proc.returncode==0 and csv_file.is_file() else "failed"
                error=None if state=="completed" else f"exit={proc.returncode}; see execution.log"
                db.execute("""UPDATE experiments SET state=?,finished_at=?,
                    elapsed_seconds=?,error=? WHERE run_id=?""",
                    (state,datetime.now(timezone.utc).isoformat(),elapsed,error,key))
                db.commit()
                completed+=1
            if state in ("completed","completed_cached"):
                persist_thresholds(db,key,csv_file,thresholds)
            run_ids.append(key)
            runs.append({"window":window,"run_id":key,"state":state,
                         "folder":str(folder)})
        results=collect_rows(db,run_ids,thresholds)
        save_reports(output,results,runs,definition)
        return {"output":str(output),"runs":runs,
                "completed":sum(r["state"] in ("completed","completed_cached")
                                for r in runs),
                "score_rows":len(results)}
    finally:
        db.close()


def main(argv=None):
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--dataset",type=Path,required=True)
    parser.add_argument("--output",type=Path,required=True)
    parser.add_argument("--analyser-root",type=Path)
    parser.add_argument("--max-chirps",type=int,default=30)
    parser.add_argument("--skip-chirps",type=int,default=3)
    parser.add_argument("--windows",type=parse_ints,default=DEFAULT_WINDOWS,
                        help="Comma-separated window sizes: 3,4,5,7")
    parser.add_argument("--thresholds",type=parse_floats,default=DEFAULT_THRESHOLDS,
                        help="Comma-separated thresholds: 0.75,1,1.5,2,2.5")
    parser.add_argument("--fft-size",type=int,default=1024)
    parser.add_argument("--stft-hop",type=int,default=32)
    parser.add_argument("--reassignment-hop",type=int,default=16)
    parser.add_argument("--spectral-alpha",type=float,default=0.0)
    parser.add_argument("--max-trials",type=int,
                        help="Limit NEW acoustic runs in this invocation")
    parser.add_argument("--retry-failed",action="store_true")
    args=parser.parse_args(argv)
    try:
        result=run_campaign(args.dataset,args.output,
                           analyser_root=args.analyser_root,
                           max_chirps=args.max_chirps,
                           skip_chirps=args.skip_chirps,
                           windows=args.windows,thresholds=args.thresholds,
                           fft_size=args.fft_size,stft_hop=args.stft_hop,
                           reassignment_hop=args.reassignment_hop,
                           spectral_alpha=args.spectral_alpha,
                           max_trials=args.max_trials,retry_failed=args.retry_failed)
    except (ValueError,FileNotFoundError,sqlite3.Error) as exc:
        parser.exit(2,f"Error: {exc}\n")
    print(json.dumps(result,indent=2,ensure_ascii=False))
    return 0


if __name__=="__main__":
    raise SystemExit(main())
