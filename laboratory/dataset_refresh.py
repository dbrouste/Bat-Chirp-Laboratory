"""Refresh one existing annotation JSON in a frozen dataset, preserving splits.

Only reference annotations may change. WAV bytes, session membership and the
calibration/test assignment must remain unchanged. No source files are modified.
"""
from __future__ import annotations

import argparse
import csv
import hashlib
import json
import os
from pathlib import Path
import shutil
import tempfile

from .dataset_builder import chirp_counts, validate_manual_curves, verify_annotation_format


def digest(path: Path) -> str:
    sha = hashlib.sha256()
    with path.open("rb") as stream:
        for chunk in iter(lambda: stream.read(1024 * 1024), b""):
            sha.update(chunk)
    return sha.hexdigest()


def within(root: Path, relative: str) -> Path:
    path = (root / relative).resolve()
    if not path.is_relative_to(root):
        raise ValueError(f"manifest path outside dataset: {relative}")
    return path


def refresh_annotations(dataset: Path, wav_name: str | None = None, *,
                        dataset_wav_path: str | None = None,
                        source_root_override: Path | None = None,
                        allow_test: bool = False, dry_run: bool = False) -> dict:
    root = dataset.resolve()
    manifest = root / "manifest.csv"
    if not manifest.is_file():
        raise ValueError(f"manifest missing: {manifest}")
    with manifest.open("r", encoding="utf-8-sig", newline="") as stream:
        reader = csv.DictReader(stream)
        fields = reader.fieldnames
        if not fields:
            raise ValueError("empty manifest")
        rows = list(reader)
    if dataset_wav_path is not None:
        # Full dataset-relative path disambiguates same-named WAVs in other sessions.
        wanted = dataset_wav_path.replace("\\\\", "/").replace("\\", "/").casefold()
        matches = [r for r in rows if r["dataset_wav"].replace("\\", "/").casefold() == wanted]
    elif wav_name is not None:
        matches = [r for r in rows if Path(r["source_wav"]).name.casefold() == wav_name.casefold()]
    else:
        raise ValueError("a WAV basename or dataset-relative WAV path is required")
    if len(matches) != 1:
        raise ValueError(f"expected one matching WAV for {dataset_wav_path or wav_name!r}, found {len(matches)}")
    selected = matches[0]
    if selected["partition"] == "test" and not allow_test:
        raise ValueError("test partition is locked; do not tune it during calibration")
    source_root = Path(source_root_override).resolve() if source_root_override else None

    def original_path(record: dict, kind: str) -> Path:
        if source_root is None:
            return Path(record["source_" + kind]).resolve()
        # Mirrors the source-root relocation in Bat-Acoustic-Analyser.
        prefix = f'{record["partition"]}/{("wav" if kind == "wav" else "annotations")}/'
        relative = record["dataset_" + kind].replace("\\", "/")
        if not relative.casefold().startswith(prefix.casefold()):
            raise ValueError(f"invalid dataset {kind} path: {relative}")
        candidate = (source_root / relative[len(prefix):]).resolve()
        if not candidate.is_relative_to(source_root):
            raise ValueError(f"source {kind} path outside selected source root")
        return candidate

    source_json = original_path(selected, "json")
    dataset_json = within(root, selected["dataset_json"])
    related = [r for r in rows if r["dataset_json"] == selected["dataset_json"]]
    if not source_json.is_file() or not dataset_json.is_file():
        raise ValueError("source JSON or existing dataset JSON missing")
    if any(original_path(r, "json") != source_json for r in related):
        raise ValueError("manifest JSON source conflict")
    source = json.loads(source_json.read_text(encoding="utf-8-sig"))
    original = json.loads(dataset_json.read_text(encoding="utf-8-sig"))
    verify_annotation_format(source, source_json)
    verify_annotation_format(original, dataset_json)
    old_files, new_files = original["files"], source["files"]
    if not isinstance(old_files, dict) or not isinstance(new_files, dict):
        raise ValueError("invalid files mapping")
    # Exact key membership prevents silently dropping or adding recordings.
    if set(old_files) != set(new_files):
        raise ValueError("JSON WAV membership changed; rebuild a new dataset version")
    dataset_wav_names = {Path(r["source_wav"]).name for r in related}
    if dataset_wav_names != set(old_files) or len(related) != len(old_files):
        raise ValueError("manifest and JSON file list disagree")
    new_counts = {}
    for r in related:
        name = Path(r["source_wav"]).name
        if not isinstance(new_files[name], dict):
            raise ValueError(f"invalid annotation entry for {name}")
        new_counts[name] = chirp_counts(new_files[name])
        validate_manual_curves(new_files[name], name)
        source_wav = original_path(r, "wav")
        dataset_wav = within(root, r["dataset_wav"])
        # Do not silently replace audio or allow source WAV changes.
        if not source_wav.is_file() or not dataset_wav.is_file():
            raise ValueError(f"WAV missing: {name}")
        if digest(source_wav) != r["wav_sha256"] or digest(dataset_wav) != r["wav_sha256"]:
            raise ValueError(f"WAV changed: {name}; rebuild a dataset instead")
    manual_before = sum(int(r["validated_manual"]) for r in related)
    new_hash = digest(source_json)
    old_hash = digest(dataset_json)
    changed = new_hash != old_hash
    for r in related:
        name = Path(r["source_wav"]).name
        manual, pending, automatic = new_counts[name]
        r.update(json_sha256=new_hash, validated_manual=str(manual),
                 manual_not_validated=str(pending),
                 automatic_proposals=str(automatic))
    result = {"wav": Path(selected["source_wav"]).name,
              "dataset_wav": selected["dataset_wav"],
              "partition": selected["partition"],
              "json": str(source_json), "affected_wavs": len(related),
              "manual_chirps_before": manual_before,
              "manual_chirps_in_updated_json": sum(c[0] for c in new_counts.values()),
              "changed": changed, "dry_run": dry_run}
    # Do not rewrite files if JSON identical.
    if dry_run or not changed:
        return result

    # Stage both files first; keep a durable backup of the previous metadata.
    # Restore the original JSON if replacing the manifest fails.
    backup = root / "previous_metadata"
    backup.mkdir(exist_ok=True)
    stamp = hashlib.sha256((str(dataset_json)+old_hash).encode()).hexdigest()[:16]
    saved_json = backup / f"{stamp}.json"
    saved_manifest = backup / f"{stamp}.manifest.csv"
    if not saved_json.exists():
        shutil.copy2(dataset_json, saved_json)
    if not saved_manifest.exists():
        shutil.copy2(manifest, saved_manifest)
    staged_json = None
    staged_manifest = None
    try:
        with tempfile.NamedTemporaryFile(dir=dataset_json.parent, delete=False) as tmp:
            staged_json = Path(tmp.name)
            with source_json.open("rb") as stream:
                shutil.copyfileobj(stream, tmp)
        with tempfile.NamedTemporaryFile("w", encoding="utf-8-sig", newline="",
                                         dir=root, delete=False) as tmp:
            staged_manifest = Path(tmp.name)
            writer = csv.DictWriter(tmp, fieldnames=fields)
            writer.writeheader()
            writer.writerows(rows)
        os.replace(staged_json, dataset_json)
        os.replace(staged_manifest, manifest)
    except Exception:
        shutil.copy2(saved_json, dataset_json)
        shutil.copy2(saved_manifest, manifest)
        raise
    finally:
        if staged_json:
            staged_json.unlink(missing_ok=True)
        if staged_manifest:
            staged_manifest.unlink(missing_ok=True)
    result["backup_dir"] = str(backup)
    return result


def refresh_all_changed(dataset: Path, *, source_root_override: Path | None = None,
                        dry_run: bool = False) -> dict:
    """Synchronize changed calibration JSONs once each; never touch test data.

    First preflight every changed session so a broken manifest or a replaced WAV
    aborts before any corpus file is updated.
    """
    root = Path(dataset).resolve()
    with (root / "manifest.csv").open(encoding="utf-8-sig", newline="") as stream:
        rows = list(csv.DictReader(stream))
    sessions: dict[str, dict] = {}
    for row in rows:
        if row.get("partition") != "calibration":
            continue
        key = row["dataset_json"].replace("\\", "/").casefold()
        sessions.setdefault(key, row)
    checks = [
        refresh_annotations(
            root, dataset_wav_path=row["dataset_wav"],
            source_root_override=source_root_override, dry_run=True)
        for row in sessions.values()
    ]
    changed = [check for check in checks if check["changed"]]
    summary = {
        "dataset": str(root), "dry_run": dry_run,
        "sessions_checked": len(checks), "sessions_changed": len(changed),
        "sessions_refreshed": 0,
        "affected_wavs": sum(check["affected_wavs"] for check in changed),
        "changed_sessions": [
            {"json": check["json"], "dataset_wav": check["dataset_wav"],
             "affected_wavs": check["affected_wavs"]}
            for check in changed],
        "test_partition_untouched": True,
    }
    if not dry_run:
        for check in changed:
            refresh_annotations(
                root, dataset_wav_path=check["dataset_wav"],
                source_root_override=source_root_override)
            summary["sessions_refreshed"] += 1
    return summary


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--dataset", type=Path, required=True)
    selection = parser.add_mutually_exclusive_group(required=True)
    selection.add_argument("--wav", help="WAV basename (must be unique in manifest)")
    selection.add_argument("--dataset-wav", help="Exact dataset-relative WAV path")
    selection.add_argument("--all-changed", action="store_true",
                           help="Safely refresh all changed calibration JSONs")
    parser.add_argument("--source-root", type=Path,
                        help="Relocated originals root, matching the QA review dialog")
    parser.add_argument("--dry-run", action="store_true")
    parser.add_argument("--allow-test", action="store_true",
                        help="Unlock a single test session explicitly (never in batch)")
    args = parser.parse_args(argv)
    try:
        if args.all_changed:
            if args.allow_test:
                raise ValueError("--all-changed never updates the test partition")
            result = refresh_all_changed(args.dataset,
                                         source_root_override=args.source_root,
                                         dry_run=args.dry_run)
        else:
            result = refresh_annotations(args.dataset, args.wav,
                                         dataset_wav_path=args.dataset_wav,
                                         source_root_override=args.source_root,
                                         dry_run=args.dry_run,
                                         allow_test=args.allow_test)
    except (ValueError, OSError, KeyError, TypeError, json.JSONDecodeError) as exc:
        parser.exit(2, f"Error: {exc}\\n")
    print(json.dumps(result, indent=2, ensure_ascii=False))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
