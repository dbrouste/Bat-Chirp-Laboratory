"""Bounded local agent for reproducible detector parameter optimization.

The agent chooses experiments adaptively from a finite, reviewed parameter
space. It does not use an LLM, rewrite code, train on held-out test recordings,
or treat unmatched candidates as false positives.

Usage: python -m laboratory.autonomous_optimizer --dataset ... --out ...
"""
from __future__ import annotations

import argparse
import csv
import hashlib
import html
import json
import os
import tempfile
from datetime import datetime, timezone
from pathlib import Path

from . import runner
from .frozen_dataset import collect_frozen_dataset

SCHEMA = "detector_agent_v1"
DEFAULTS = {
    "v4_general_min_width_ms": None,
    "search_margin_ms": None,
    "n_fft": None,
    "hop": None,
    "trim_echo_rebound": True,
}
COLUMNS = ("trial", "stage", "eligible", "reason", "score", "recall",
           "weighted_recall", "full_pipeline", "weighted_full_pipeline",
           "high_snr_recall", "geometry_khz_eq", "coverage", "candidates",
           "annotated", "negative_wavs", "negative_alarm_wavs",
           "negative_candidates", "status", "parameters", "report_path")


def _laboratory_fingerprint() -> str:
    """Fingerprint the benchmark aggregation and the agent's ranking policy."""
    root = Path(__file__).resolve().parents[1]
    files = (
        "laboratory/autonomous_optimizer.py",
        "laboratory/runner.py",
        "laboratory/aggregation.py",
        "laboratory/frozen_dataset.py",
        "laboratory/report.py",
        "chirp_geometry/__init__.py",
    )
    hashes = {}
    for name in files:
        source = root / name
        hashes[name] = hashlib.sha256(source.read_bytes()).hexdigest()
    return hashlib.sha256(
        json.dumps(hashes, sort_keys=True).encode("utf-8")
    ).hexdigest()


def _id(options: dict) -> str:
    raw = json.dumps(options, sort_keys=True, separators=(",", ":"))
    return hashlib.sha256(raw.encode()).hexdigest()[:14]


def _write_json(path: Path, data: dict) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with tempfile.NamedTemporaryFile(
        mode="w", encoding="utf-8", dir=path.parent,
        prefix=".agent_", suffix=".tmp", delete=False,
    ) as output:
        tmp = Path(output.name)
        try:
            json.dump(data, output, ensure_ascii=False, indent=2,
                      allow_nan=False)
            output.write("\n")
        except BaseException:
            tmp.unlink(missing_ok=True)
            raise
    os.replace(tmp, path)


def metrics(report: dict) -> dict:
    totals = report["totals"]
    refs = int(totals["annotated"])
    if report["metadata"]["status"] != "complete" or refs <= 0:
        raise ValueError("Incomplete benchmark or no validated manual chirps")
    high = totals["by_snr"]["high_35_db_or_more"]
    neg = totals.get("reviewed_no_chirp") or {}
    return {
        "annotated": refs,
        "matched": int(totals["matched"]),
        "recall": float(totals["matched"]) / refs,
        "weighted_recall": float(totals["weighted_detector_recall"]),
        "full_pipeline": float(totals["full_pipeline_within_tolerance_fraction"]),
        "weighted_full_pipeline": float(
            totals["weighted_full_pipeline_within_tolerance_fraction"]),
        "high_snr_count": int(high["annotated"]),
        "high_snr_recall": (
            float(high["detector_recall"])
            if high["detector_recall"] is not None else None),
        "geometry_khz_eq": totals.get("mean_geometric_error_khz_eq"),
        "coverage": totals.get("mean_geometric_coverage_fraction"),
        "candidates": int(totals["candidates"]),
        "negative_wavs": int(neg.get("wav_count", 0)),
        "negative_alarm_wavs": int(neg.get("wavs_with_candidate_alert", 0)),
        "negative_candidates": int(neg.get("candidate_alerts", 0)),
        "negative_false_alarm_fraction": neg.get("wav_false_alarm_fraction"),
    }


def judge(candidate: dict, baseline: dict) -> tuple[bool, str, float]:
    """Keep hard non-regression gates separate from the scalar ranking."""
    if candidate["annotated"] != baseline["annotated"]:
        return False, "reference count changed", -1.0
    if candidate["high_snr_count"] != baseline["high_snr_count"]:
        return False, "high SNR reference count changed", -1.0
    neg_count = int(baseline.get("negative_wavs", 0))
    if int(candidate.get("negative_wavs", 0)) != neg_count:
        return False, "reviewed negative cohort changed", -1.0
    if neg_count:
        if int(candidate.get("negative_alarm_wavs", 0)) > int(
            baseline.get("negative_alarm_wavs", 0)
        ):
            return False, "more no_chirp WAVs trigger detector candidates", -1.0
        if int(candidate.get("negative_candidates", 0)) > int(
            baseline.get("negative_candidates", 0)
        ):
            return False, "more detector candidates in reviewed no_chirp WAVs", -1.0
    if candidate["high_snr_recall"] is None:
        return False, "high SNR references unavailable", -1.0
    if candidate["high_snr_recall"] < baseline["high_snr_recall"] - 0.02:
        return False, "high SNR recall regressed by >2 percentage points", -1.0
    if candidate["weighted_full_pipeline"] < (
        baseline["weighted_full_pipeline"] - 0.02
    ):
        return False, "trajectory score regressed by >2 percentage points", -1.0
    if (baseline["geometry_khz_eq"] is not None
            and baseline["geometry_khz_eq"] > 0
            and (candidate["geometry_khz_eq"] is None
                 or candidate["geometry_khz_eq"]
                 > baseline["geometry_khz_eq"] * 1.2)):
        return False, "geometric mean error grew by >20%", -1.0
    # Reference WAVs are only partially annotated. Unmatched candidates
    # might be genuine but unannotated calls: neither candidate volume nor
    # its reduction is evidence of precision, specificity or echo rejection.
    # Candidate count remains diagnostic only (e.g. compute profiling).
    score = (0.15 * candidate["recall"]
             + 0.40 * candidate["weighted_recall"]
             + 0.45 * candidate["weighted_full_pipeline"])
    if neg_count:
        # Only explicitly reviewed negative WAVs can inform false alarms.
        score -= 0.10 * (
            int(candidate.get("negative_alarm_wavs", 0)) / neg_count
        )
        score -= 0.01 * min(
            2.0, int(candidate.get("negative_candidates", 0)) / neg_count
        )
    return True, "valid", round(score, 6)


def _leader(trials: list[dict]) -> dict:
    valid = [t for t in trials if t.get("eligible") is True]
    if not valid:
        raise ValueError("No successful baseline")
    return max(valid, key=lambda t: t["score"])


def _plans(best: dict, attempts: list[dict]) -> list[tuple[str, dict]]:
    """Finite and staged search: later hypotheses use the best prior result."""
    tried = {t["id"] for t in attempts}
    base = dict(DEFAULTS)

    def one(stage: str, overrides: dict):
        settings = {**base, **overrides}
        if _id(settings) not in tried:
            return stage, settings
        return None

    # Candidate generation first; only after that tune tracking.
    for width in (1.0, 0.85, 1.65):
        option = one("candidate-width", {"v4_general_min_width_ms": width})
        if option:
            return [option]
    # Expand tracking around the best configuration seen so far.
    best_opts = dict(best["options"])
    for margin in (2.0, 3.0, 1.0):
        option = one("tracking-margin", {
            **best_opts, "search_margin_ms": margin})
        if option:
            return [option]
    # Probe waveform/trajectory reconstruction, not new scoring weights.
    for fft, hop in ((512, 16), (256, 16), (512, 32)):
        option = one("reassignment", {
            **best_opts, "n_fft": fft, "hop": hop})
        if option:
            return [option]
    option = one("echo-tail", {**best_opts, "trim_echo_rebound": False})
    return [option] if option else []


def _cases(report: dict, *, limit: int = 300) -> list[dict]:
    problems = []
    for item in report.get("details", []):
        missed = not bool(item.get("detector_matched"))
        geometry = item.get("mean_geometric_error_khz_eq")
        coverage = item.get("geometric_coverage_fraction")
        start = item.get("start_error_ms")
        end = item.get("end_error_ms")
        issues = []
        if missed:
            issues.append("not_detected")
        else:
            if geometry is not None and geometry > 3:
                issues.append("geometry_above_3_khz_eq")
            if coverage is not None and coverage < 0.85:
                issues.append("coverage_below_85_pct")
            if start is not None and start > 0.5:
                issues.append("start_error_above_0.5_ms")
            if end is not None and end > 1.5:
                issues.append("end_error_above_1.5_ms")
        if not issues:
            continue
        snr = item.get("snr_max_db")
        priority = (
            100 if missed and snr is not None and snr >= 35
            else 80 if missed
            else 65 if geometry is not None and geometry > 3
            else 40
        )
        problems.append({
            "file": item.get("file"),
            "chirp_id": item.get("chirp_id"),
            "issues": ";".join(issues),
            "priority": priority,
            "snr_max_db": snr,
            "geometric_error_khz_eq": geometry,
            "coverage": coverage,
            "start_error_ms": start,
            "end_error_ms": end,
        })
    return sorted(problems,
                  key=lambda x: (-x["priority"], -(x["snr_max_db"] or 0),
                                 x["file"] or ""))[:limit]


def _export(output: Path, state: dict) -> None:
    trials = state["trials"]
    baseline = next((t for t in trials if t["stage"] == "baseline"
                     and t["status"] == "complete"), None)
    selected = _leader(trials) if baseline else None
    summary = {
        "schema": SCHEMA,
        "dataset_provenance": state["dataset_provenance"],
        "algorithm_fingerprint": state["algorithm_fingerprint"],
        "laboratory_fingerprint": state["laboratory_fingerprint"],
        "started_utc": state["started_utc"],
        "updated_utc": datetime.now(timezone.utc).isoformat(),
        "trial_count": len(trials),
        "successful_trials": sum(t["status"] == "complete" for t in trials),
        "baseline": baseline,
        "recommended": selected if selected and selected != baseline else None,
        "recommendation_requires_human_approval": True,
        "test_partition_accessed": False,
        "annotations_exhaustive": False,
        "precision_and_false_positive_rate_measurable": False,
        "reviewed_no_chirp_false_candidate_metrics_measurable": bool(
            baseline and baseline["metrics"].get("negative_wavs", 0) > 0),
        "note": (
            "All rankings are calibration-only. This agent searches parameter "
            "settings without modifying detector source or dataset. An unmatched "
            "candidate is NOT evidence of a false positive, even inside "
            "WAVs with manual annotations: annotations are not exhaustive. "
            "Counts on partially annotated positive WAVs never impact ranking. "
            "Explicit reviewed no_chirp WAVs supply separate false candidate "
            "alarm metrics and non-regression gates if available. "
            "Geometric SNR tolerances remain uncalibrated. "
            "Do not deploy automatically."
        ),
    }
    _write_json(output / "agent_summary.json", summary)
    with (output / "leaderboard.csv").open(
        "w", encoding="utf-8-sig", newline=""
    ) as stream:
        writer = csv.DictWriter(stream, fieldnames=COLUMNS)
        writer.writeheader()
        for t in sorted(trials, key=lambda v: v.get("score", -1), reverse=True):
            m = t.get("metrics") or {}
            writer.writerow({
                "trial": t["id"], "stage": t["stage"],
                "eligible": t.get("eligible", False),
                "reason": t.get("reason", t.get("error", "")),
                "score": t.get("score", ""),
                "recall": m.get("recall", ""),
                "weighted_recall": m.get("weighted_recall", ""),
                "full_pipeline": m.get("full_pipeline", ""),
                "weighted_full_pipeline": m.get("weighted_full_pipeline", ""),
                "high_snr_recall": m.get("high_snr_recall", ""),
                "geometry_khz_eq": m.get("geometry_khz_eq", ""),
                "coverage": m.get("coverage", ""),
                "candidates": m.get("candidates", ""),
                "annotated": m.get("annotated", ""),
                "negative_wavs": m.get("negative_wavs", ""),
                "negative_alarm_wavs": m.get("negative_alarm_wavs", ""),
                "negative_candidates": m.get("negative_candidates", ""),
                "status": t["status"],
                "parameters": json.dumps(t["options"], ensure_ascii=False),
                "report_path": t.get("report_path", ""),
            })
    if baseline:
        # Review errors from the selected run, not the training corpus itself.
        best_report = output / selected["report_path"]
        with best_report.open(encoding="utf-8") as stream:
            problems = _cases(json.load(stream))
        with (output / "priority_cases.csv").open(
            "w", encoding="utf-8-sig", newline=""
        ) as stream:
            fields = ("file", "chirp_id", "issues", "priority", "snr_max_db",
                      "geometric_error_khz_eq", "coverage",
                      "start_error_ms", "end_error_ms")
            writer = csv.DictWriter(stream, fieldnames=fields)
            writer.writeheader()
            writer.writerows(problems)

    table = []
    for trial in sorted(trials, key=lambda t: t.get("score", -1), reverse=True):
        metric = trial.get("metrics", {})
        row = [trial["stage"], trial["status"],
               trial.get("reason", trial.get("error", "")),
               str(trial.get("score", "")),
               f'{100 * metric["recall"]:.1f}%' if metric else "",
               f'{100 * metric["weighted_full_pipeline"]:.1f}%' if metric else "",
               (f'{metric.get("negative_alarm_wavs", 0)}/'
                f'{metric.get("negative_wavs", 0)}' if metric and metric.get("negative_wavs", 0) else "—"),
               str(trial["options"])]
        table.append("<tr>" + "".join(
            "<td>" + html.escape(v) + "</td>" for v in row
        ) + "</tr>")
    markup = ("<!doctype html><html lang='fr'><meta charset='utf-8'>"
              "<title>Chirp Lab — Agent local</title>"
              "<style>body{font:15px system-ui;margin:2rem auto;"
              "max-width:1250px;color:#243243}td,th{padding:.55rem;"
              "border-bottom:1px solid #ddd;text-align:left}"
              "table{border-collapse:collapse;width:100%}</style>"
              "<h1>Agent local — optimisation du détecteur</h1>"
              "<p>Calibration uniquement. Aucune modification du code ou "
              "du corpus. Une suggestion ne vaut pas validation finale.</p>"
              "<p><strong>Configuration conseillée :</strong> "
              + html.escape(str(selected["options"]) if selected else "indisponible")
              + "</p><table><tr><th>Étape</th><th>État</th>"
              "<th>Règle</th><th>Score</th><th>Rappel</th>"
              "<th>Suivi pondéré</th><th>WAV no_chirp avec alertes</th>"
              "<th>Paramètres</th></tr>"
              + "".join(table) + "</table></html>")
    (output / "agent_report.html").write_text(markup, encoding="utf-8")


def run_agent(*, dataset: Path, out: Path, processing_root: Path,
              workers: int = 4, max_total: int = 11,
              max_new: int | None = None) -> dict:
    dataset = Path(dataset).resolve()
    out = Path(out).resolve()
    processing_root = Path(processing_root).resolve()
    if max_total < 1 or max_total > 30:
        raise ValueError("max_total must be between 1 and 30")
    if max_new is not None and max_new < 1:
        raise ValueError("max_new must be positive")
    if out == dataset or out.is_relative_to(dataset):
        raise ValueError("Agent output must be outside frozen dataset")
    if not 1 <= workers <= 8:
        raise ValueError("workers must be 1..8")
    if not (processing_root / "benchmark"
            / "reassignment_detector_seeds.py").is_file():
        raise ValueError("Invalid processing-root: external benchmark missing")

    # Read only the CALIBRATION manifest and verify all frozen hashes.
    _, provenance = collect_frozen_dataset(dataset, partition="calibration")
    algorithm_fingerprint = runner.code_fingerprint(processing_root)
    laboratory_fingerprint = _laboratory_fingerprint()
    out.mkdir(parents=True, exist_ok=True)
    lock = out / ".agent.lock"
    try:
        fd = os.open(lock, os.O_CREAT | os.O_EXCL | os.O_WRONLY)
    except FileExistsError as exc:
        raise RuntimeError(
            f"Campaign already running or stale lock: {lock}") from exc
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as f:
            f.write(f"pid={os.getpid()}\n")
        state_path = out / "agent_state.json"
        if state_path.is_file():
            state = json.loads(state_path.read_text(encoding="utf-8"))
            if (state.get("schema") != SCHEMA
                    or state.get("dataset_provenance") != provenance
                    or state.get("algorithm_fingerprint") != algorithm_fingerprint
                    or state.get("laboratory_fingerprint") != laboratory_fingerprint):
                raise ValueError(
                    "Agent state no longer matches dataset/algorithm. "
                    "Start a NEW --out directory; never mix baselines.")
        else:
            state = {
                "schema": SCHEMA,
                "dataset_provenance": provenance,
                "algorithm_fingerprint": algorithm_fingerprint,
                "laboratory_fingerprint": laboratory_fingerprint,
                "started_utc": datetime.now(timezone.utc).isoformat(),
                "trials": [],
            }
        fresh = 0
        while len(state["trials"]) < max_total and (
            max_new is None or fresh < max_new
        ):
            existing = state["trials"]
            if not existing:
                stage, options = "baseline", dict(DEFAULTS)
            else:
                baseline = next(
                    (t for t in existing if t["stage"] == "baseline"
                     and t["status"] == "complete"), None)
                if baseline is None:
                    break
                proposed = _plans(_leader(existing), existing)
                if not proposed:
                    break
                stage, options = proposed[0]
            trial_id = _id(options)
            trial_dir = out / "trials" / trial_id
            try:
                report = runner.run(
                    dataset=dataset, out=trial_dir, workers=workers,
                    processing_root=processing_root, options=options,
                    include_reviewed_negatives=True)
                measure = metrics(report)
                if stage == "baseline":
                    eligible, reason, score = judge(measure, measure)
                else:
                    reference = baseline["metrics"]
                    eligible, reason, score = judge(measure, reference)
                trial = {
                    "id": trial_id, "stage": stage, "options": options,
                    "status": "complete", "metrics": measure,
                    "eligible": eligible, "reason": reason, "score": score,
                    "report_path": str(Path("trials") / trial_id / "report.json"),
                }
            except Exception as exc:
                trial = {
                    "id": trial_id, "stage": stage, "options": options,
                    "status": "failed", "eligible": False,
                    "error": f"{type(exc).__name__}: {exc}",
                    "reason": "benchmark failed", "score": -1.0,
                }
            state["trials"].append(trial)
            fresh += 1
            _write_json(state_path, state)
            _export(out, state)
            print(json.dumps({
                "trial": len(state["trials"]), "stage": stage,
                "status": trial["status"],
                "score": trial.get("score"),
                "eligible": trial["eligible"],
                "reason": trial.get("reason"),
            }, ensure_ascii=False), flush=True)
            if stage == "baseline" and trial["status"] != "complete":
                break
        if state["trials"]:
            _export(out, state)
        complete = [t for t in state["trials"]
                    if t["status"] == "complete"]
        return {
            "schema": SCHEMA,
            "output": str(out),
            "trials_total": len(state["trials"]),
            "trials_complete": len(complete),
            "new_trials_this_invocation": fresh,
            "recommended": _leader(complete)["options"] if complete else None,
            "report": str(out / "agent_report.html"),
            "needs_human_approval": True,
        }
    finally:
        lock.unlink(missing_ok=True)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--dataset", type=Path, required=True)
    parser.add_argument("--out", type=Path, required=True)
    parser.add_argument("--processing-root", type=Path,
                        default=runner.DEFAULT_PROCESSING_ROOT)
    parser.add_argument("--workers", type=int, default=4)
    parser.add_argument("--max-total", type=int, default=11)
    parser.add_argument("--max-new", type=int, default=None,
                        help="Cap NEW runs this invocation; can resume later")
    args = parser.parse_args(argv)
    try:
        result = run_agent(
            dataset=args.dataset, out=args.out,
            processing_root=args.processing_root,
            workers=args.workers, max_total=args.max_total,
            max_new=args.max_new)
    except (ValueError, RuntimeError, FileNotFoundError, OSError) as exc:
        parser.exit(2, f"Error: {exc}\n")
    print(json.dumps(result, ensure_ascii=False, indent=2))
    return 0 if result["trials_complete"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
