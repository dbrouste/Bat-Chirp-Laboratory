"""Windows-compatible, restartable local benchmark runner.

Run from the repository root:
  python -m laboratory --annotations D:\\Bat\\annotations --wav-dir D:\\Bat\\WAV
"""

from __future__ import annotations

import argparse
from concurrent.futures import ProcessPoolExecutor, as_completed
from datetime import datetime, timezone
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
                      write_manifest)
from .aggregation import summarize_results
from .frozen_dataset import collect_frozen_dataset
from .fingerprints import implementation_fingerprint


ROOT = Path(__file__).resolve().parents[1]
DEFAULT_PROCESSING_ROOT = ROOT.parent / "bat_full_spectrum_processing"
DEFAULT_OPTIONS = {
    "n_fft": None, "hop": None, "max_per_file": None,
    "v4_general_min_width_ms": None, "search_margin_ms": None,
    "trim_echo_rebound": True, "low_snr_importance_weight": 0.2,
    "geometry_mode": "square_normals_v1",
}


def code_fingerprint(processing_root: Path) -> str:
    """Include source edits from both repositories, not only Git commit IDs."""
    return implementation_fingerprint({
        "laboratory": (ROOT, ("laboratory", "chirp_geometry")),
        "processing": (processing_root, ("bat_analysis", "benchmark")),
    })


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
            processing_root: str) -> dict:
    """One WAV per worker: no shared mutable processing state."""
    started = perf_counter()
    try:
        # The detector is an external checkout; no library code is copied
        # into or modified by the laboratory.
        if processing_root not in sys.path:
            sys.path.insert(0, processing_root)
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
        if (isinstance(data.get("per_file"), list) and data["per_file"]
                and isinstance(data.get("details"), list) and "traceback" not in data
                and all(isinstance(row, dict) and "error" not in row for row in data["per_file"])
                and all(isinstance(row, dict) for row in data["details"])):
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
            meta["duration_s"], totals["weighted_detector_recall"],
            totals["weighted_full_pipeline_within_tolerance_fraction"],
            str(output.resolve()),
        ))


def remove_excluded_references(records: dict[str, dict]) -> tuple[dict[str, dict], int]:
    """Remove only manually annotated chirps explicitly excluded by human QA.

    WAVs and source JSONs are untouched; other chirps in the same recording
    remain available. Never interpret unannotated events as review failures.
    """
    filtered = {}
    excluded = 0
    for name, record in records.items():
        updated = []
        for chirp in record.get("chirps", []):
            quality = chirp.get("annotation_quality") if isinstance(chirp, dict) else None
            if (isinstance(chirp, dict)
                    and chirp.get("source") == "manual"
                    and chirp.get("status") == "validated"
                    and isinstance(quality, dict)
                    and quality.get("status") == "excluded"):
                excluded += 1
                continue
            updated.append(chirp)
        filtered[name] = {**record, "chirps": updated}
    return filtered, excluded


def run(*, annotations: Path | None = None, wav_dir: Path | None = None,
        dataset: Path | None = None, out: Path, workers: int = 4,
        max_files: int | None = None, options: dict | None = None,
        use_cache: bool = True, include_reviewed_negatives: bool = False,
        processing_root: Path = DEFAULT_PROCESSING_ROOT) -> dict:
    """Evaluate WAVs independently and aggregate using the existing benchmark.

    Unlike a parameter sweep, a run never writes to the input WAV or annotation
    directories. Cache keys include full audio/reference/code/option content.
    """
    if not 1 <= workers <= 8:
        raise ValueError("workers must be between 1 and 8")
    if include_reviewed_negatives and dataset is None:
        raise ValueError("Reviewed negatives require a frozen --dataset")
    if max_files is not None and max_files < 1:
        raise ValueError("max_files must be positive")
    params = dict(DEFAULT_OPTIONS)
    if options:
        unknown = set(options) - set(params)
        if unknown:
            raise ValueError(f"Unknown options: {sorted(unknown)}")
        params.update(options)
    if not 0 < params["low_snr_importance_weight"] <= 1:
        raise ValueError("low_snr_importance_weight must be in (0, 1]")
    if params["geometry_mode"] not in ("square_normals_v1", "vertical_legacy"):
        raise ValueError("invalid geometry_mode")

    processing_root = Path(processing_root).resolve()
    if not (processing_root / "bat_analysis" / "detection.py").is_file() or not (
            processing_root / "benchmark" / "reassignment_detector_seeds.py").is_file():
        raise ValueError("--processing-root must point to a checkout of "
                         "bat_full_spectrum_processing with the SNR benchmark")
    if params["geometry_mode"] == "square_normals_v1":
        processing_evaluator = (processing_root / "benchmark"
                                / "reassignment_detector_seeds.py")
        # A stale sibling checkout would reject the geometry_mode argument
        # in each worker, silently producing a failed/incomplete experiment.
        if "geometry_mode=" not in processing_evaluator.read_text(encoding="utf-8"):
            raise ValueError(
                "bat_full_spectrum_processing checkout is outdated: "
                "run 'git pull origin main' in that repository before "
                "enabling square_normals_v1")
    out = Path(out).resolve()
    if dataset is not None:
        if annotations is not None or wav_dir is not None:
            raise ValueError("--dataset cannot be combined with --annotations/--wav-dir")
        dataset_root = Path(dataset).resolve()
        if out == dataset_root or out.is_relative_to(dataset_root):
            raise ValueError("Benchmark output must be outside the frozen dataset")
        records, dataset_provenance = collect_frozen_dataset(dataset_root)
        wav_dir = dataset_root
        annotation_description = dataset_root / "manifest.csv"
    else:
        if annotations is None or wav_dir is None:
            raise ValueError("Use --dataset or both --annotations and --wav-dir")
        wav_dir = Path(wav_dir).resolve()
        records = collect_annotations(Path(annotations), wav_dir)
        dataset_provenance = None
        annotation_description = Path(annotations).resolve()
    out.mkdir(parents=True, exist_ok=True)
    # Validate contradictory negative labels BEFORE excluding any curves.
    # "no_chirp" is a reviewer decision, not equivalent to no annotations.
    if dataset_provenance is not None:
        for name, rec in records.items():
            if rec.get("review_status") != "no_chirp":
                continue
            chirps = rec.get("chirps") or []
            if not isinstance(chirps, list):
                raise ValueError(f"Invalid chirp list for {name}")
            if any(
                isinstance(c, dict) and (
                    c.get("source") == "manual"
                    or c.get("status") == "validated"
                    or c.get("edited")
                ) for c in chirps
            ):
                raise ValueError(
                    f"Conflicting no_chirp review and manually reviewed chirp: {name}")
    records, excluded_count = remove_excluded_references(records)
    # The annotated recordings are NOT exhaustive: unannotated regions
    # remain unknown. Only explicitly reviewed 'no_chirp' WAVs may
    # serve as negative references.
    negative_names: set[str] = set()
    if dataset_provenance is not None:
        before = len(records)
        positives = {
            name: rec for name, rec in records.items()
            if any(
                isinstance(chirp, dict)
                and chirp.get("source") == "manual"
                and chirp.get("status") == "validated"
                for chirp in rec.get("chirps", [])
            )
        }
        negatives = {
            name: rec for name, rec in records.items()
            if rec.get("review_status") == "no_chirp"
        }
        dataset_provenance["wav_with_reference"] = len(positives)
        dataset_provenance["reviewed_no_chirp_available"] = len(negatives)
        dataset_provenance["wav_without_reference_skipped"] = (
            before - len(positives)
            - (len(negatives) if include_reviewed_negatives else 0)
        )
        if not positives:
            raise ValueError(
                "No validated manual chirps in the frozen calibration partition")
        # Preserve the old max-files semantics: select positives first.
        records = dict(list(positives.items())[:max_files])
        if include_reviewed_negatives:
            remaining = (None if max_files is None
                         else max_files - len(records))
            records.update(dict(list(negatives.items())[:remaining]))
            negative_names = set(records) & set(negatives)
        dataset_provenance["reviewed_no_chirp_evaluated"] = len(negative_names)
    elif max_files is not None:
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
                           "code": source_hash, "options": params})
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
                             str(processing_root))
            results[name] = result
            if "traceback" not in result and not any(
                    "error" in row for row in result["per_file"]):
                _write_json_atomic(cache_file, result)
    else:
        # Spawn explicitly to enforce Windows semantics during development.
        with ProcessPoolExecutor(max_workers=workers,
                                 mp_context=multiprocessing.get_context("spawn")) as pool:
            futures = {pool.submit(_worker, name, record, str(wav_dir), params,
                                   str(processing_root)):
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
    totals = summarize_results(per_file, details,
                               low_snr_importance_weight=params[
                                   "low_snr_importance_weight"])
    for row in per_file:
        if row["file"] in negative_names:
            row["reference_class"] = "reviewed_no_chirp"
    reviewed = [row for row in per_file if row["file"] in negative_names
                and "error" not in row]
    false_alarm_wavs = sum(int(row.get("candidates", 0)) > 0
                          for row in reviewed)
    false_candidate_alerts = sum(int(row.get("candidates", 0))
                                 for row in reviewed)
    # Duration is metadata-only, no audio re-analysis. Diagnostic rates are
    # omitted if headers cannot be read; candidate counts are still valid.
    neg_duration_s = None
    if reviewed:
        import soundfile as sf
        try:
            neg_duration_s = sum(
                float(sf.info(str(Path(wav_dir) / row["file"])).duration)
                for row in reviewed
            )
        except (OSError, RuntimeError, ValueError):
            neg_duration_s = None
    totals["reviewed_no_chirp"] = {
        "wav_count": len(reviewed),
        "wavs_with_candidate_alert": false_alarm_wavs,
        "candidate_alerts": false_candidate_alerts,
        "wav_false_alarm_fraction": (
            false_alarm_wavs / len(reviewed) if reviewed else None),
        "candidate_alerts_per_wav": (
            false_candidate_alerts / len(reviewed) if reviewed else None),
        "duration_seconds": neg_duration_s,
        "candidate_alerts_per_hour": (
            3600 * false_candidate_alerts / neg_duration_s
            if neg_duration_s and neg_duration_s > 0 else None),
        "metric_scope": "detector candidates, not fully tracked chirps",
    }
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
        "ground_truth": "manual + validated, excluding annotation_quality=excluded",
        "excluded_manual_references": excluded_count,
        "positive_annotations_exhaustive": False,
        "reviewed_negative_label": "review_status=no_chirp",
        "include_reviewed_negatives": include_reviewed_negatives,
        "scoring_protocol": params["geometry_mode"],
        "geometry_distance_unit": ("kHz_eq" if params["geometry_mode"] == "square_normals_v1"
                                   else "kHz_vertical"),
        "geometric_tolerances_calibrated": False,
        "annotations": str(annotation_description), "wav_dir": str(wav_dir),
        "frozen_dataset": dataset_provenance,
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
    input_mode = parser.add_mutually_exclusive_group(required=True)
    input_mode.add_argument("--dataset", type=Path,
                            help="Frozen dataset-v1 manifest, calibration only")
    input_mode.add_argument("--annotations", type=Path,
                            help="Single JSON or directory with annotation JSONs")
    parser.add_argument("--wav-dir", type=Path,
                        help="Required with --annotations; not used with --dataset")
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
    parser.add_argument("--include-reviewed-negatives", action="store_true",
                        help="Also evaluate explicit no_chirp WAVs from frozen calibration")
    args = parser.parse_args(argv)
    if args.dataset is None and args.wav_dir is None:
        parser.error("--wav-dir is required with --annotations")
    if args.dataset is not None and args.wav_dir is not None:
        parser.error("--wav-dir must not be used with --dataset")
    report = run(annotations=args.annotations, wav_dir=args.wav_dir,
                 dataset=args.dataset,
                 out=args.out, workers=args.workers, max_files=args.max_files,
                 use_cache=not args.no_cache,
                 include_reviewed_negatives=args.include_reviewed_negatives,
                 processing_root=args.processing_root,
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
                      "reviewed_no_chirp": totals["reviewed_no_chirp"],
                      "weighted_detector_recall": totals["weighted_detector_recall"],
                      "duration_s": report["metadata"]["duration_s"],
                      "report": str((args.out / "report.html").resolve())},
                     ensure_ascii=False, indent=2))
    return 0 if report["metadata"]["status"] == "complete" else 1
