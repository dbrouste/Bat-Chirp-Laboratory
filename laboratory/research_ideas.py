"""Inspect the scientific research backlog without running detector experiments.

The YAML file contains human hypotheses, not executable code or proof.
Only "ready_for_code_agent" entries with an implemented matching benchmark
are eligible for code-edit campaigns. Current H01-H04 need benchmark labels.
"""
from __future__ import annotations

import argparse
import json
from pathlib import Path

CATALOG = Path(__file__).resolve().parents[1] / "experiments" / "ideas.yaml"
SUPPORTED_STATUSES = {
    "needs_benchmark", "ready_for_code_agent", "paused", "completed",
}
REQUIRED_FIELDS = {"id", "title", "priority", "status", "hypothesis",
                   "experiments", "metrics_needed", "depends_on"}


def read_ideas(path: Path = CATALOG) -> dict:
    import yaml

    filepath = Path(path)
    with filepath.open(encoding="utf-8") as stream:
        document = yaml.safe_load(stream)
    if not isinstance(document, dict) or document.get("schema") != "chirp_research_ideas_v1":
        raise ValueError("Unsupported research idea catalog schema")
    entries = document.get("ideas")
    if not isinstance(entries, list) or not entries:
        raise ValueError("The research backlog is empty")
    seen = set()
    for entry in entries:
        if not isinstance(entry, dict) or not REQUIRED_FIELDS.issubset(entry):
            raise ValueError("A research idea is missing required fields")
        id_ = entry["id"]
        if not isinstance(id_, str) or not id_.startswith("H") or not id_[1:].isdigit():
            raise ValueError(f"Invalid research idea ID: {id_!r}")
        if id_ in seen:
            raise ValueError(f"Duplicate research idea ID: {id_}")
        seen.add(id_)
        if entry["status"] not in SUPPORTED_STATUSES:
            raise ValueError(f"Unknown research idea status: {entry['status']!r}")
        for field in ("hypothesis", "title"):
            if not isinstance(entry[field], str) or not entry[field].strip():
                raise ValueError(f"{id_}: missing {field}")
        for field in ("experiments", "metrics_needed", "depends_on"):
            if not isinstance(entry[field], list):
                raise ValueError(f"{id_}: {field} must be a list")
    return document


def select_idea(idea_id: str, *, path: Path = CATALOG,
                for_code_agent: bool = False) -> dict:
    entries = read_ideas(path)["ideas"]
    entry = next((x for x in entries if x["id"] == idea_id), None)
    if entry is None:
        raise ValueError(f"Unknown research idea {idea_id!r}")
    if for_code_agent:
        if entry["status"] != "ready_for_code_agent":
            raise ValueError(
                f"{idea_id} is {entry['status']}: its benchmark and "
                "reference labels must be prepared before Codex can "
                "optimize it safely")
        # Strict authorization for the currently implemented code pilot.
        # New research domains need their own metrics, tests and file
        # allowlists; a YAML edit alone cannot elevate permissions.
        if entry.get("benchmark") != "detector_baseline_v1":
            raise ValueError(
                f"{idea_id}: unsupported benchmark for the current code agent")
        if entry.get("allowed_files") != ["bat_analysis/detection.py"]:
            raise ValueError(
                f"{idea_id}: code pilot is limited to detection.py")
    return entry


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--file", type=Path, default=CATALOG)
    parser.add_argument("--id", dest="idea_id")
    parser.add_argument("--json", action="store_true")
    args = parser.parse_args(argv)
    try:
        entries = read_ideas(args.file)["ideas"]
        if args.idea_id:
            entries = [select_idea(args.idea_id, path=args.file)]
    except (OSError, ValueError) as exc:
        parser.exit(2, f"Error: {exc}\n")
    if args.json:
        print(json.dumps(entries, ensure_ascii=False, indent=2))
    else:
        for item in entries:
            print(f"{item['id']} [{item['status']}] "
                  f"({item['priority']}): {item['title']}")
        print("Status needs_benchmark = research only; not runnable by Codex.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
