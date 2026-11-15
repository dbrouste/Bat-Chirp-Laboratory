"""Read a frozen Bat-Chirp-Laboratory manifest as read-only benchmark input.

Only the named partition and the WAVs explicitly listed in manifest.csv are
considered. Directory-wide JSONs may contain other WAVs in the originals, but
the frozen dataset snapshot and its manifest must agree exactly.
"""
from __future__ import annotations

import csv
import json
from pathlib import Path, PureWindowsPath

from .dataset import sha256_file


def _within(dataset: Path, relative: str) -> Path:
    text = str(relative or "").replace("\\", "/")
    windows = PureWindowsPath(text)
    if not text or windows.is_absolute() or windows.drive:
        raise ValueError(f"Invalid frozen dataset path: {relative!r}")
    result = (dataset / text).resolve()
    if not result.is_relative_to(dataset) or result == dataset:
        raise ValueError(f"Frozen dataset path escapes its root: {relative!r}")
    return result


def collect_frozen_dataset(dataset: Path, *, partition: str = "calibration",
                           verify_hashes: bool = True) -> tuple[dict[str, dict], dict]:
    """Return a safe per-WAV input mapping and audit metadata.

    Test is deliberately NOT accepted as an argument here. The final held-out
    evaluation will have a separate explicit procedure, not a casual flag.
    """
    if partition != "calibration":
        raise ValueError("Only calibration can be used for baseline development")
    root = Path(dataset).resolve()
    manifest = root / "manifest.csv"
    if not manifest.is_file():
        raise ValueError(f"Dataset manifest missing: {manifest}")
    with manifest.open(encoding="utf-8-sig", newline="") as stream:
        reader = csv.DictReader(stream)
        required = {"partition", "session", "dataset_wav", "dataset_json",
                    "wav_sha256", "json_sha256"}
        if not required.issubset(reader.fieldnames or []):
            raise ValueError("Dataset manifest is missing required columns")
        rows = list(reader)
    if not rows:
        raise ValueError("Dataset manifest is empty")

    # Select only calibration. Check split consistency for a shared JSON too.
    by_json: dict[str, set[str]] = {}
    for row in rows:
        path = str(row["dataset_json"] or "").replace("\\", "/").casefold()
        by_json.setdefault(path, set()).add(str(row["partition"]))
    conflicting = [name for name, splits in by_json.items() if len(splits) != 1]
    if conflicting:
        raise ValueError("One annotation JSON appears in multiple partitions")

    selected = [r for r in rows if r["partition"] == partition]
    if not selected:
        raise ValueError("No calibration WAV in frozen dataset manifest")

    records: dict[str, dict] = {}
    json_cache: dict[Path, dict] = {}
    json_rows: dict[Path, list[dict]] = {}
    seen_paths: set[str] = set()
    for row in selected:
        raw_wav = str(row["dataset_wav"] or "").replace("\\", "/")
        norm = raw_wav.casefold()
        if norm in seen_paths:
            raise ValueError(f"Duplicate dataset WAV: {raw_wav}")
        seen_paths.add(norm)
        prefix = f"{partition}/wav/"
        if not raw_wav.casefold().startswith(prefix):
            raise ValueError(f"Invalid partition WAV path: {raw_wav!r}")
        wav = _within(root, raw_wav)
        raw_json = str(row["dataset_json"] or "").replace("\\", "/")
        if not raw_json.casefold().startswith(f"{partition}/annotations/"):
            raise ValueError(f"Invalid partition JSON path: {raw_json!r}")
        annotation = _within(root, raw_json)
        if not wav.is_file() or not annotation.is_file():
            raise FileNotFoundError(f"Frozen dataset WAV/JSON missing: {raw_wav}")
        if verify_hashes:
            if not row["wav_sha256"] or sha256_file(wav).lower() != row["wav_sha256"].lower():
                raise ValueError(f"Frozen WAV fingerprint mismatch: {raw_wav}")
            if not row["json_sha256"]:
                raise ValueError(f"Missing frozen JSON fingerprint: {raw_json}")
        if annotation not in json_cache:
            if verify_hashes and sha256_file(annotation).lower() != row["json_sha256"].lower():
                raise ValueError(f"Frozen JSON fingerprint mismatch: {raw_json}")
            doc = json.loads(annotation.read_text(encoding="utf-8-sig"))
            if not isinstance(doc, dict) or not isinstance(doc.get("files"), dict):
                raise ValueError(f"Invalid frozen annotation mapping: {raw_json}")
            json_cache[annotation] = doc["files"]
        elif verify_hashes and sha256_file(annotation).lower() != row["json_sha256"].lower():
            raise ValueError(f"Manifest has conflicting JSON hashes: {raw_json}")
        json_rows.setdefault(annotation, []).append(row)
        key = wav.name
        mapping = json_cache[annotation]
        if key not in mapping or not isinstance(mapping[key], dict):
            raise ValueError(f"Frozen JSON has no matching WAV entry: {key}")
        records[raw_wav] = mapping[key]

    for annotation, related in json_rows.items():
        expected = {Path(str(r["dataset_wav"]).replace("\\", "/")).name for r in related}
        if expected != set(json_cache[annotation]) or len(expected) != len(related):
            raise ValueError(
                f"Manifest and frozen JSON WAV membership differ: {annotation}")

    return dict(sorted(records.items())), {
        "dataset": str(root),
        "partition": partition,
        "manifest_sha256": sha256_file(manifest),
        "wav_count": len(records),
        "session_count": len(json_rows),
        "hashes_verified": verify_hashes,
    }
