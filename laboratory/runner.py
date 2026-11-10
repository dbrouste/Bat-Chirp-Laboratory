"""Windows-compatible, restartable local benchmark runner.

Run from the repository root:
  python -m laboratory --annotations D:\\Bat\\annotations --wav-dir D:\\Bat\\WAV
"""

from __future__ import annotations

import argparse
from concurrent.futures import ProcessPoolExecutor, as_completed
from dataclasses import asdict
from datetime import datetime, timezone
import importlib.metadata
import json
import multiprocessing
import os
from pathlib import Path
import shutil
import sqlite3
import subprocess
import sys
import tempfile
from time import perf_counter
import traceback

from .dataset import (collect_annotations, fingerprint_inputs, stable_hash,
                      sha256_file, write_manifest)
from .aggregation import summarize_results


ROOT = Path(__file__).resolve().parents[1]
DEFAULT_PROCESSING_ROOT = ROOT.parent / "bat_full_spectrum_processing"
DEFAULT_OPTIONS = {
    "n_fft": None, "hop": None, "max_per_file": None,
    "v4_general_min_width_ms": None, "search_margin_ms": None,
    "trim_echo_rebound": True, "low_snr_importance_weight": 0.2,
}


def code_fingerprint(processing_root: Path) -> str:
    """Include source edits from both repositories, not only Git commit IDs."""
    files = sorted([*(ROOT / "laboratory").rglob("*.py"),
                    *(processing_root / "bat_analysis").rglob("*.py"),
                    *(processing_root / "benchmark").rglob("*.py")])
    files.extend([ROOT / "requirements.txt", processing_root / "requirements.txt"])
    metadata = [(str(p.resolve()), sha256_file(p))
                for p in files if p.is_file()]
    versions = {}
    for package in ("numpy", "scipy", "soundfile"):
        try:
            versions[package] = importlib.metadata.version(package)
        except importlib.metadata.PackageNotFoundError:
            versions[package] = None
    return stable_hash({"files": metadata, "dependencies": versions,
                        "python": sys.version_info[:3]})


def git_info(repo: Path) -> dict:
    try:
        sha = subprocess.check_output(["git", "rev-parse", "HEAD"], cwd=repo,
                                      stderr=subprocess.DEVNULL, text=True).strip()
        dirty = bool(subprocess.check_output(["git", "status", "--porcelain"],
                                             cwd=repo, stderr=subprocess.DEVNULL,
                                             text=True).strip())
        return {"git_commit": sha, "git_dirty": dirty}
    except (OSError, subprocess.CalledProcessError):
        return {"git_commit": None, "git_dirty": None}


def _worker(name: str, record: dict, wav_root: str, options: dict,
            processing_root: str, scoring: str = "snr",
            geometry_options: dict | None = None) -> dict:
    """One WAV per worker: no shared mutable processing state."""
    started = perf_counter()
    try:
        # The detector is an external checkout; no library code is copied
        # into or modified by the laboratory.
        if processing_root not in sys.path:
            sys.path.insert(0, processing_root)
        if scoring == "geometry":
            from .geometry_benchmark import evaluate_file
            report = evaluate_file(name, record, wav_root, options,
                                   geometry_options or {})
        else:
            from benchmark.reassignment_detector_seeds import evaluate

            with tempfile.TemporaryDirectory(prefix="chirp_lab_") as folder:
                annotation = Path(folder) / "single.json"
                annotation.write_text(json.dumps({"files": {name: record}},
                                                 ensure_ascii=False), encoding="utf-8")
                report = evaluate(annotation, wav_root, **options)
        return {"file": name, "per_file": report["per_file"],
                "details": report["details"], "elapsed_s": perf_counter() - started}
    except Exception as exc:
        return {"file": name,
                "per_file": [{"file": name, "error": f"{type(exc).__name__}: {exc}"}],
                "details": [], "elapsed_s": perf_counter() - started,
                "traceback": traceback.format_exc()}


def _read_cache(path: Path) -> dict | None:
    if not path.is_file():
        return None
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
        if isinstance(data.get("per_file"), list) and isinstance(data.get("details"), list):
            return data
    except (OSError, ValueError, AttributeError):
        pass
    return None


def _write_json_atomic(path: Path, value: dict) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    # Unique temp name: safe even when campaigns are launched close together.
    with tempfile.NamedTemporaryFile("w", encoding="utf-8", dir=path.parent,
                                     prefix=".partial_", suffix=".json",
                                     delete=False) as stream:
        temporary = Path(stream.name)
        try:
            json.dump(value, stream, ensure_ascii=False, indent=2, allow_nan=False)
        except BaseException:
            stream.close()
            temporary.unlink(missing_ok=True)
            raise
    os.replace(temporary, path)


def _record_experiment(db: Path, report: dict, output: Path) -> None:
    db.parent.mkdir(parents=True, exist_ok=True)
    with sqlite3.connect(db) as connection:
        connection.execute("""CREATE TABLE IF NOT EXISTS experiments (
            run_id TEXT PRIMARY KEY, created_utc TEXT NOT NULL,
            status TEXT NOT NULL, git_commit TEXT, code_fingerprint TEXT,
            dataset_fingerprint TEXT, parameters_json TEXT NOT NULL,
            duration_seconds REAL NOT NULL, weighted_recall REAL,
            weighted_following REAL, report_path TEXT NOT NULL)""")
        meta = report["metadata"]
        totals = report["totals"]
        connection.execute("""INSERT OR REPLACE INTO experiments
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""", (
            meta["run_id"], meta["created_utc"], meta["status"],
            meta["git_commit"], meta["code_fingerprint"],
            meta["dataset_fingerprint"], json.dumps(meta["options"]),
            meta["duration_s"], totals.get("weighted_detector_recall",
                                           totals.get("weighted_recall")),
            totals.get("weighted_full_pipeline_within_tolerance_fraction",
                       totals.get("weighted_trajectory_score")),
            str(output.resolve()),
        ))


def run(*, annotations: Path, wav_dir: Path, out: Path, workers: int = 4,
        max_files: int | None = None, options: dict | None = None,
        use_cache: bool = True,
        processing_root: Path = DEFAULT_PROCESSING_ROOT, scoring: str = "snr",
        geometry_config: dict | None = None) -> dict:
    """Evaluate WAVs independently and aggregate using the existing benchmark.

    Unlike a parameter sweep, a run never writes to the input WAV or annotation
    directories. Cache keys include full audio/reference/code/option content.
    """
    if not 1 <= workers <= 8:
        raise ValueError("workers must be between 1 and 8")
    if max_files is not None and max_files < 1:
        raise ValueError("max_files must be positive")
    if scoring not in {"snr", "geometry"}:
        raise ValueError("scoring must be 'snr' or 'geometry'")
    if scoring == "geometry":
        from .geometry import GeometryConfig
        geometry_config = dict(geometry_config or {})
        geometry_config = asdict(GeometryConfig(**geometry_config))
    elif geometry_config:
        raise ValueError("geometry_config requires scoring='geometry'")
    params = dict(DEFAULT_OPTIONS)
    if options:
        unknown = set(options) - set(params)
        if unknown:
            raise ValueError(f"Unknown options: {sorted(unknown)}")
        params.update(options)
    if not 0 < params["low_snr_importance_weight"] <= 1:
        raise ValueError("low_snr_importance_weight must be in (0, 1]")

    processing_root = Path(processing_root).resolve()
    if not (processing_root / "bat_analysis" / "detection.py").is_file() or not (
            processing_root / "benchmark" / "reassignment_detector_seeds.py").is_file():
        raise ValueError("--processing-root must point to a checkout of "
                         "bat_full_spectrum_processing with the SNR benchmark")
    wav_dir = Path(wav_dir).resolve()
    out = Path(out).resolve()
    out.mkdir(parents=True, exist_ok=True)
    records = collect_annotations(Path(annotations), wav_dir)
    if max_files is not None:
        records = dict(list(records.items())[:max_files])
    inputs = fingerprint_inputs(records, wav_dir)
    source_hash = code_fingerprint(processing_root)
    dataset_hash = stable_hash(inputs)
    started = perf_counter()

    # Must be inherited by spawned workers before scipy/NumPy import.
    for var in ("OMP_NUM_THREADS", "OPENBLAS_NUM_THREADS", "MKL_NUM_THREADS",
                "NUMEXPR_NUM_THREADS"):
        os.environ[var] = "1"

    cache_root = out / "cache"
    pending = []
    results = {}
    cache_count = 0
    for name, record in records.items():
        key = stable_hash({"name": name, "input": inputs[name],
                           "code": source_hash, "options": params,
                           "scoring": scoring, "geometry_config": geometry_config})
        cache_file = cache_root / f"{key}.json"
        cached = _read_cache(cache_file) if use_cache else None
        if cached is None:
            pending.append((name, record, cache_file))
        else:
            results[name] = cached
            cache_count += 1
    if workers == 1:
        for name, record, cache_file in pending:
            result = _worker(name, record, str(wav_dir), params,
                             str(processing_root), scoring, geometry_config)
            results[name] = result
            if "traceback" not in result and not any(
                    "error" in row for row in result["per_file"]):
                _write_json_atomic(cache_file, result)
    else:
        # Spawn explicitly to enforce Windows semantics during development.
        with ProcessPoolExecutor(max_workers=workers,
                                 mp_context=multiprocessing.get_context("spawn")) as pool:
            futures = {pool.submit(_worker, name, record, str(wav_dir), params,
                                   str(processing_root), scoring, geometry_config):
                       (name, cache_file) for name, record, cache_file in pending}
            for future in as_completed(futures):
                name, cache_file = futures[future]
                try:
                    result = future.result()
                except Exception as exc:
                    result = {"file": name,
                              "per_file": [{"file": name,
                                            "error": f"Worker failed: {exc}"}],
                              "details": [], "elapsed_s": 0}
                results[name] = result
                if "traceback" not in result and not any(
                        "error" in row for row in result["per_file"]):
                    _write_json_atomic(cache_file, result)

    ordered = [results[name] for name in records]
    per_file = [row for item in ordered for row in item["per_file"]]
    details = [row for item in ordered for row in item["details"]]
    if scoring == "geometry":
        from .geometry_benchmark import aggregate
        totals = aggregate(per_file, details)
    else:
        totals = summarize_results(per_file, details,
                                   low_snr_importance_weight=params[
                                       "low_snr_importance_weight"])
    now = datetime.now(timezone.utc)
    duration = perf_counter() - started
    status = "complete" if totals["files_missing"] == 0 else "incomplete"
    algorithm_git = git_info(processing_root)
    laboratory_git = git_info(ROOT)
    meta = {
        "run_id": now.strftime("%Y%m%dT%H%M%S%fZ"),
        "created_utc": now.isoformat(), "status": status, **algorithm_git,
        "algorithm_git": algorithm_git, "laboratory_git": laboratory_git,
        "processing_root": str(processing_root),
        "code_fingerprint": source_hash, "dataset_fingerprint": dataset_hash,
        "ground_truth": "manual + validated only",
        "scoring_protocol": ("geometry v1 (uncalibrated thresholds unless provided)"
                             if scoring == "geometry" else
                             "existing SNR trajectory benchmark"),
        "scoring": scoring, "geometry_config": geometry_config,
        "annotations": str(Path(annotations).resolve()), "wav_dir": str(wav_dir),
        "options": params, "workers": workers,
        "files_from_cache": cache_count,
        "duration_s": duration,
        "per_file_duration_s": {row["file"]: row["elapsed_s"] for row in ordered},
        "input_fingerprints": inputs,
    }
    report = {"metadata": meta, "totals": totals,
              "per_file": per_file, "details": details}
    run_dir = out / "runs" / meta["run_id"]
    json_path = run_dir / "report.json"
    _write_json_atomic(json_path, report)
    write_manifest(records, wav_dir, inputs, run_dir / "manifest.csv")
    from .report import write_html

    html_path = run_dir / "report.html"
    write_html(report, html_path)
    # Convenience pointers to the latest run, while historical DB paths stay valid.
    shutil.copyfile(json_path, out / "report.json")
    shutil.copyfile(html_path, out / "report.html")
    shutil.copyfile(run_dir / "manifest.csv", out / "manifest.csv")
    _record_experiment(out / "experiments.sqlite", report, json_path)
    return report


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--annotations", type=Path, required=True,
                        help="Single JSON or directory with annotation JSONs")
    parser.add_argument("--wav-dir", type=Path, required=True)
    parser.add_argument("--processing-root", type=Path,
                        default=DEFAULT_PROCESSING_ROOT,
                        help="External bat_full_spectrum_processing checkout")
    parser.add_argument("--out", type=Path, default=Path("laboratory-results"))
    parser.add_argument("--workers", type=int, default=4)
    parser.add_argument("--max-files", type=int, default=None)
    parser.add_argument("--max-per-file", type=int, default=None)
    parser.add_argument("--n-fft", type=int, default=None)
    parser.add_argument("--hop", type=int, default=None)
    parser.add_argument("--general-min-width-ms", type=float, default=None)
    parser.add_argument("--search-margin-ms", type=float, default=None)
    parser.add_argument("--low-snr-weight", type=float, default=0.2)
    parser.add_argument("--no-echo-trim", action="store_true")
    parser.add_argument("--no-cache", action="store_true")
    parser.add_argument("--scoring", choices=("snr", "geometry"), default="snr")
    parser.add_argument("--geometry-config", type=Path, default=None,
                        help="Calibration JSON (from python -m laboratory.calibration)")
    args = parser.parse_args(argv)
    if args.geometry_config is not None and args.scoring != "geometry":
        parser.error("--geometry-config requires --scoring geometry")
    geometry_options = None
    if args.geometry_config is not None:
        contents = json.loads(args.geometry_config.read_text(encoding="utf-8"))
        geometry_options = contents.get("config", contents)
    report = run(annotations=args.annotations, wav_dir=args.wav_dir,
                 out=args.out, workers=args.workers, max_files=args.max_files,
                 use_cache=not args.no_cache,
                 processing_root=args.processing_root,
                 scoring=args.scoring, geometry_config=geometry_options,
                 options={"n_fft": args.n_fft, "hop": args.hop,
                          "max_per_file": args.max_per_file,
                          "v4_general_min_width_ms": args.general_min_width_ms,
                          "search_margin_ms": args.search_margin_ms,
                          "trim_echo_rebound": not args.no_echo_trim,
                          "low_snr_importance_weight": args.low_snr_weight})
    totals = report["totals"]
    print(json.dumps({"status": report["metadata"]["status"],
                      "wav_evaluated": totals["files_evaluated"],
                      "manual_chirps": totals["annotated"],
                      "weighted_detector_recall": totals.get("weighted_detector_recall",
                                                             totals.get("weighted_recall")),
                      "duration_s": report["metadata"]["duration_s"],
                      "report": str((args.out / "report.html").resolve())},
                     ensure_ascii=False, indent=2))
    return 0 if report["metadata"]["status"] == "complete" else 1
