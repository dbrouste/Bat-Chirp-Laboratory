"""One outer Windows Job covering the supervisor and every calculation worker."""

import argparse
import json
from pathlib import Path
import sys
import time
import uuid

from . import campaign_control as control
from .windows_resources import campaign_lock, run_bounded


def usage(folder):
    records = []
    for reservation in sorted((folder / "global_runs").glob("*/reservation.json")):
        row = json.loads(reservation.read_text(encoding="utf-8"))
        outcome = reservation.with_name("outcome.json")
        if outcome.exists():
            row.update(json.loads(outcome.read_text(encoding="utf-8")))
        records.append(row)
    return records


def run_global(folder, *, resume=False, max_new=None, retry_failed=False):
    folder = Path(folder).resolve()
    definition = control.load_definition(folder)
    with campaign_lock(folder, "global.lock"):
        charged = sum(row.get("elapsed_s", row["reserved_s"]) for row in usage(folder))
        remaining = definition["limits"]["campaign_seconds"] - charged
        if remaining <= 0:
            raise RuntimeError("Global campaign wall-time budget exhausted")
        attempt_id = uuid.uuid4().hex
        directory = folder / "global_runs" / attempt_id
        directory.mkdir(parents=True)
        control.runner._write_json_atomic(directory / "reservation.json", {
            "reserved_s": remaining, "started_utc": control.utc_now(), "definition": definition["fingerprint"]})
        command = [sys.executable, "-m", "laboratory.global_campaign", "--campaign", str(folder), "--attempt", attempt_id]
        if resume:
            command.append("--resume")
        if retry_failed:
            command.append("--retry-failed")
        if max_new is not None:
            command.extend(["--max-new", str(max_new)])
        outcome = run_bounded(command, folder=folder, gate=directory / "start.gate",
            log=directory / "execution.log", seconds=remaining,
            memory_bytes=definition["limits"]["memory_bytes"],
            min_free_bytes=definition["limits"]["min_free_bytes"], stop_requested=lambda: False)
        outcome["finished_utc"] = control.utc_now()
        outcome["scope"] = "supervisor_and_all_descendant_workers_commit_memory"
        if outcome["status"] in {"complete", "failed"}:
            try:
                receipt = json.loads((directory / "receipt.json").read_text(encoding="utf-8"))
                if receipt.get("definition") != definition["fingerprint"] or receipt.get("attempt") != attempt_id:
                    raise ValueError("Global supervisor receipt mismatch")
            except (OSError, ValueError) as exc:
                outcome.update(status="failed", error=f"Global supervisor did not complete safely: {exc}")
                with control.database(folder) as connection:
                    connection.execute("UPDATE detector_control SET status='failed',error=?", (outcome["error"],))
        control.runner._write_json_atomic(directory / "outcome.json", outcome)
        if outcome["status"] in {"timed_out", "interrupted", "disk_guard"}:
            with control.database(folder) as connection:
                connection.execute("UPDATE detector_control SET status=?,error=?", (outcome["status"], outcome["error"]))
                connection.execute("UPDATE detector_jobs SET status='interrupted' WHERE status='running'")
                connection.execute("UPDATE detector_attempts SET status='interrupted' WHERE status='running'")
        report = control.export_reports(folder)
        report["global_supervision"] = usage(folder)
        control.runner._write_json_atomic(folder / "campaign_report.json", report)
        return report


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--campaign", type=Path, required=True)
    parser.add_argument("--attempt", required=True)
    parser.add_argument("--resume", action="store_true")
    parser.add_argument("--retry-failed", action="store_true")
    parser.add_argument("--max-new", type=int)
    args = parser.parse_args(argv)
    gate = args.campaign / "global_runs" / args.attempt / "start.gate"
    deadline = time.monotonic() + 30
    while not gate.exists():
        if time.monotonic() >= deadline:
            raise RuntimeError("Global Windows Job was not assigned")
        time.sleep(0.05)
    report = control.run_campaign(args.campaign, resume=args.resume, max_new=args.max_new, retry_failed=args.retry_failed)
    control.runner._write_json_atomic(gate.parent / "receipt.json", {
        "definition": report["definition"], "attempt": args.attempt, "status": report["status"]})
    return 0 if report["status"] in {"paused", "complete"} else 1


if __name__ == "__main__":
    raise SystemExit(main())
