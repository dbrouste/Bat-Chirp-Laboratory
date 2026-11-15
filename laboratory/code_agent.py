"""Reviewable, bounded Codex code-edit pilot on isolated local Git worktrees.

Only the original code checkout and frozen calibration data are benchmarked.
Codex sees code (not the dataset path), is instructed to edit ONE allowlisted
file, and is never permitted to merge, push or deploy. Controller gates each
candidate by Git status, local tests and a full calibration benchmark.
"""
from __future__ import annotations

import argparse
import json
import os
import shutil
import subprocess
import sys
import tempfile
from datetime import datetime, timezone
from pathlib import Path

from . import runner
from .autonomous_optimizer import judge, metrics
from .frozen_dataset import collect_frozen_dataset

SCHEMA = "codex_detector_pilot_v1"
ALLOWLIST = {"bat_analysis/detection.py"}
TESTS = ("tests/test_snr_benchmark.py", "tests/test_tracking_margin.py")
GOALS = (
    "Improve medium-SNR (15-35 dB) candidate recall, especially short "
    "calls, without sacrificing high-SNR recall.",
    "Reduce overlapping-call candidate suppression, preserving one-to-one "
    "matching on strong calls and existing dedup safeguards.",
    "Improve weak-end candidate support by making a small, well-tested "
    "change to candidate extraction, avoiding echo and noise flooding.",
)


def _atomic_json(path: Path, value: dict) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with tempfile.NamedTemporaryFile(
        "w", encoding="utf-8", dir=path.parent,
        prefix=".tmp_", suffix=".json", delete=False
    ) as out:
        tmp = Path(out.name)
        try:
            json.dump(value, out, ensure_ascii=False, indent=2, allow_nan=False)
        except BaseException:
            tmp.unlink(missing_ok=True)
            raise
    os.replace(tmp, path)


def _git(repo: Path, *args: str) -> str:
    proc = subprocess.run(
        ["git", "-C", str(repo), *args], capture_output=True, text=True,
        encoding="utf-8", errors="replace", check=False, timeout=30)
    if proc.returncode:
        raise RuntimeError(f"Git {args[0]} failed: {proc.stderr[-1500:]}")
    # Preserve the leading status column in " M path" porcelain output.
    return proc.stdout.rstrip("\r\n")


def _verify_edit(worktree: Path, original_head: str) -> list[str]:
    if _git(worktree, "rev-parse", "HEAD") != original_head:
        raise ValueError("Codex changed Git HEAD; proposal rejected")
    raw = _git(worktree, "status", "--porcelain=v1", "--untracked-files=all")
    if not raw:
        raise ValueError("Codex proposed no source changes")
    changed = []
    for line in raw.splitlines():
        # No added, deleted, renamed, staged, or untracked files.
        if not line.startswith(" M "):
            raise ValueError(f"Unexpected Git change rejected: {line}")
        relative = line[3:].replace("\\", "/")
        if relative not in ALLOWLIST:
            raise ValueError(f"Change outside allowlist: {relative}")
        changed.append(relative)
    if not changed:
        raise ValueError("No allowlisted changes")
    _git(worktree, "diff", "--check")
    return changed


def _prompt(goal: str, baseline: dict, prior: list[dict]) -> str:
    notes = [
        f"Goal: {goal}",
        f"Baseline: {baseline['matched']} matched of {baseline['annotated']}; "
        f"high SNR recall={baseline['high_snr_recall']:.4f}; "
        f"weighted full pipeline={baseline['weighted_full_pipeline']:.4f}; "
        f"candidate count={baseline['candidates']}.",
        "You are inside a temporary Git worktree with SOURCE CODE ONLY. "
        "You do not have access to or permission to inspect user WAVs, "
        "JSON annotations, dataset paths or the held-out test partition.",
        "Make one minimal, evidence-based detector improvement. "
        "Modify ONLY bat_analysis/detection.py. "
        "Do not modify imports from third-party packages, other files, "
        "test data, Git metadata or environment settings. Do not commit, "
        "push, merge, download files, or access the network.",
        "Existing detector regression tests must remain compatible. "
        "The controller will execute tests and run the frozen "
        "calibration benchmark, and may reject your change.",
        "Do not optimize around known individual WAV identities or "
        "memorize annotations. Prioritize generalizable signal behavior.",
        "Provide a concise summary and any scientific caveats.",
    ]
    if prior:
        notes.append("Previous independent attempts and measured feedback:")
        for item in prior[-3:]:
            notes.append(f"- {item['goal']}: {item['status']}; "
                         f"{item.get('reason', item.get('error', ''))}; "
                         f"score={item.get('score')}")
    return "\n\n".join(notes) + "\n"


def _run_codex(worktree: Path, prompt: str, log: Path, timeout: int) -> None:
    cli = shutil.which("codex")
    if cli is None:
        raise ValueError(
            "Codex CLI not found. Install @openai/codex and run 'codex login'.")
    # No shell, no extra writable directories and no approval escalation.
    # The CLI's own API connection is needed for its model, but its tool
    # commands run inside workspace-write sandbox.
    # Do not pass local corpus locations to Codex as environment variables.
    model_env = {key: val for key, val in os.environ.items()
                 if not key.upper().startswith("CHIRP_")}
    try:
        result = subprocess.run(
            [cli, "exec", "--sandbox", "workspace-write",
             "--ask-for-approval", "never", "-"],
            input=prompt, capture_output=True, text=True,
            cwd=worktree, encoding="utf-8", errors="replace",
            env=model_env, check=False, timeout=timeout)
    except subprocess.TimeoutExpired as exc:
        log.write_text("Codex timed out.\n", encoding="utf-8")
        raise RuntimeError("Codex execution timed out") from exc
    log.write_text(
        "STDOUT:\n" + result.stdout + "\nSTDERR:\n" + result.stderr,
        encoding="utf-8")
    if result.returncode:
        raise RuntimeError(
            f"Codex exited with {result.returncode}; see {log}")


def _tests(worktree: Path, logfile: Path, timeout: int) -> None:
    try:
        proc = subprocess.run(
            [sys.executable, "-m", "pytest", "-q", *TESTS],
            cwd=worktree, capture_output=True, text=True,
            encoding="utf-8", errors="replace", timeout=timeout,
            check=False)
    except subprocess.TimeoutExpired as exc:
        logfile.write_text("Tests timed out.\n", encoding="utf-8")
        raise RuntimeError("Regression tests timed out") from exc
    logfile.write_text(proc.stdout + "\n" + proc.stderr, encoding="utf-8")
    if proc.returncode:
        raise RuntimeError(f"Regression tests failed; see {logfile}")


def run(*, dataset: Path, processing_root: Path, out: Path,
        parameter_agent_summary: Path, workers: int = 4,
        max_total: int = 3, max_new: int | None = None,
        codex_timeout_s: int = 900) -> dict:
    if not 1 <= max_total <= len(GOALS):
        raise ValueError("max_total must be 1..3")
    if max_new is not None and max_new < 1:
        raise ValueError("max_new must be positive")
    if not 1 <= workers <= 8:
        raise ValueError("workers must be 1..8")
    dataset = Path(dataset).resolve()
    processing_root = Path(processing_root).resolve()
    out = Path(out).resolve()
    source_summary = json.loads(
        Path(parameter_agent_summary).read_text(encoding="utf-8"))
    for protected in (dataset, processing_root,
                      Path(__file__).resolve().parents[1]):
        if out == protected or out.is_relative_to(protected):
            raise ValueError("Output must be outside corpus and source checkouts")
    if source_summary.get("schema") != "detector_agent_v1":
        raise ValueError("Expected detector_agent_v1 summary")
    candidate = (source_summary.get("recommended")
                 or source_summary.get("baseline"))
    if not candidate or not candidate.get("eligible"):
        raise ValueError("Parameter agent has no eligible baseline")
    options = dict(candidate["options"])
    if not set(options).issubset(runner.DEFAULT_OPTIONS):
        raise ValueError("Unsupported detector parameter in agent summary")

    _, provenance = collect_frozen_dataset(dataset, partition="calibration")
    if provenance != source_summary.get("dataset_provenance"):
        raise ValueError("Dataset changed since parameter campaign")
    code_hash = runner.code_fingerprint(processing_root)
    if code_hash != source_summary.get("algorithm_fingerprint"):
        raise ValueError("Detector code changed since parameter campaign")
    if _git(processing_root, "status", "--porcelain"):
        raise ValueError("Processing checkout must be clean")
    original_head = _git(processing_root, "rev-parse", "HEAD")
    if not shutil.which("codex"):
        raise ValueError("Codex CLI not found: install it and authenticate")
    out.mkdir(parents=True, exist_ok=True)
    lock = out / ".code_agent.lock"
    try:
        fd = os.open(lock, os.O_WRONLY | os.O_CREAT | os.O_EXCL)
    except FileExistsError as exc:
        raise RuntimeError("Another campaign is running or stale lock exists") from exc

    try:
        with os.fdopen(fd, "w", encoding="utf-8") as handle:
            handle.write(f"pid={os.getpid()}\n")
        state_file = out / "code_agent_state.json"
        if state_file.is_file():
            state = json.loads(state_file.read_text(encoding="utf-8"))
            if (state.get("schema") != SCHEMA
                    or state.get("provenance") != provenance
                    or state.get("code_hash") != code_hash
                    or state.get("head") != original_head
                    or state.get("options") != options):
                raise ValueError("Campaign provenance changed; use a new output")
        else:
            state = {
                "schema": SCHEMA, "provenance": provenance,
                "code_hash": code_hash, "head": original_head,
                "options": options, "started_utc": datetime.now(timezone.utc).isoformat(),
                "baseline": None, "attempts": [],
            }
        # Establish reproducible baseline with the parameter-agent incumbent.
        if state["baseline"] is None:
            original = runner.run(
                dataset=dataset, out=out / "original_baseline",
                processing_root=processing_root, workers=workers,
                options=options)
            base_metrics = metrics(original)
            state["baseline"] = base_metrics
            _atomic_json(state_file, state)
        base_metrics = state["baseline"]
        # An accepted proposal must beat BOTH the baseline and earlier winner.
        champion = {
            "score": judge(base_metrics, base_metrics)[2],
            "kind": "incumbent",
            "metrics": base_metrics,
        }
        for past in state["attempts"]:
            if past.get("eligible") and past.get("score", -1) > champion["score"]:
                champion = past
        fresh = 0
        while len(state["attempts"]) < max_total and (
            max_new is None or fresh < max_new
        ):
            i = len(state["attempts"])
            goal = GOALS[i]
            trial = out / "attempts" / f"attempt_{i+1:02d}"
            worktree = trial / "code"
            if worktree.exists():
                raise RuntimeError(
                    f"Unrecorded worktree at {worktree}; inspect manually")
            trial.mkdir(parents=True, exist_ok=True)
            result = {
                "number": i+1, "goal": goal, "status": "failed",
                "eligible": False, "score": -1.0,
                "worktree": str(worktree),
            }
            try:
                _git(processing_root, "worktree", "add",
                     "--detach", str(worktree), original_head)
                _run_codex(worktree, _prompt(goal, base_metrics,
                                            state["attempts"]),
                           trial / "codex.log", codex_timeout_s)
                result["changed"] = _verify_edit(worktree, original_head)
                if (runner.code_fingerprint(processing_root) != code_hash
                        or _git(processing_root, "status", "--porcelain")):
                    raise RuntimeError("Original processing checkout changed")
                _tests(worktree, trial / "pytest.log", timeout=240)
                report = runner.run(
                    dataset=dataset, out=trial / "benchmark",
                    processing_root=worktree, workers=workers,
                    options=options)
                measure = metrics(report)
                if (runner.code_fingerprint(processing_root) != code_hash
                        or _git(processing_root, "status", "--porcelain")):
                    raise RuntimeError("Original processing checkout changed")
                allowed, why, score = judge(measure, base_metrics)
                result.update({
                    "status": "complete", "metrics": measure,
                    "eligible": allowed, "reason": why, "score": score,
                    "report": str(trial / "benchmark" / "report.json"),
                })
                if allowed and score > champion["score"]:
                    champion = result
            except Exception as exc:
                result["reason"] = f"{type(exc).__name__}: {exc}"
            state["attempts"].append(result)
            fresh += 1
            _atomic_json(state_file, state)
            _atomic_json(out / "code_agent_summary.json", {
                "schema": SCHEMA, "baseline": state["baseline"],
                "best": champion, "attempts": state["attempts"],
                "recommendation_requires_human_approval": True,
                "test_partition_accessed": False,
                "source_checkout_unchanged": (
                    runner.code_fingerprint(processing_root) == code_hash
                    and not _git(processing_root, "status", "--porcelain")),
                "no_git_changes_promoted": True,
            })
            print(f"[{i+1}/{max_total}] {result['status']} "
                  f"eligible={result['eligible']} "
                  f"score={result['score']}", flush=True)
        return {
            "attempts_total": len(state["attempts"]),
            "new_attempts": fresh,
            "best": champion["kind"] if "kind" in champion else champion["number"],
            "summary": str(out / "code_agent_summary.json"),
        }
    finally:
        lock.unlink(missing_ok=True)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--dataset", type=Path, required=True)
    parser.add_argument("--parameter-agent-summary", type=Path, required=True)
    parser.add_argument("--out", type=Path, required=True)
    parser.add_argument("--processing-root", type=Path,
                        default=runner.DEFAULT_PROCESSING_ROOT)
    parser.add_argument("--workers", type=int, default=4)
    parser.add_argument("--max-total", type=int, default=3)
    parser.add_argument("--max-new", type=int, default=None)
    args = parser.parse_args(argv)
    try:
        result = run(
            dataset=args.dataset, processing_root=args.processing_root,
            out=args.out, parameter_agent_summary=args.parameter_agent_summary,
            workers=args.workers, max_total=args.max_total, max_new=args.max_new)
    except (OSError, ValueError, RuntimeError) as exc:
        parser.exit(2, f"Error: {exc}\n")
    print(json.dumps(result, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
