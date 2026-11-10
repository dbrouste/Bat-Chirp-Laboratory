"""Build a deterministic, validated WAV/annotation inventory."""

from __future__ import annotations

import hashlib
import csv
import json
from pathlib import Path


def sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        for block in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(block)
    return digest.hexdigest()


def stable_hash(value: object) -> str:
    data = json.dumps(value, ensure_ascii=False, sort_keys=True,
                      separators=(",", ":"), allow_nan=False)
    return hashlib.sha256(data.encode("utf-8")).hexdigest()


def collect_annotations(annotations: Path, wav_root: Path) -> dict[str, dict]:
    """Accept one annotation JSON or a recursive directory of annotation JSONs.

    WAV names are normalized relative to wav_root, which makes IDs unique even
    when distinct subdirectories contain the same basename. Ambiguity fails
    loudly; neither reference files nor human annotations are ever rewritten.
    """
    annotations = annotations.resolve()
    wav_root = wav_root.resolve()
    if not wav_root.is_dir():
        raise ValueError(f"WAV directory does not exist: {wav_root}")
    if annotations.is_file():
        files = [annotations]
    elif annotations.is_dir():
        files = sorted(annotations.rglob("*.json"))
    else:
        raise ValueError(f"Annotations path not found: {annotations}")
    records: dict[str, dict] = {}
    sources: dict[str, str] = {}
    for source in files:
        try:
            payload = json.loads(source.read_text(encoding="utf-8"))
        except (UnicodeError, json.JSONDecodeError) as exc:
            if annotations.is_file():
                raise ValueError(f"Invalid annotation JSON: {source}") from exc
            continue  # Non-annotation JSON in a recursive directory.
        if not isinstance(payload, dict) or not isinstance(payload.get("files"), dict):
            if annotations.is_file():
                raise ValueError(f"Missing 'files' mapping: {source}")
            continue
        for name, record in payload["files"].items():
            if not isinstance(name, str) or not isinstance(record, dict):
                raise ValueError(f"Invalid WAV entry in {source}: {name!r}")
            # A per-directory JSON usually names just 'same.wav'. Map the
            # JSON's relative directory onto the WAV tree before falling back
            # to absolute-root or next-to-JSON conventions.
            candidates = []
            if annotations.is_dir():
                relative_folder = source.parent.relative_to(annotations)
                candidates.append((wav_root / relative_folder / name).resolve())
            candidates.extend(((wav_root / name).resolve(),
                               (source.parent / name).resolve()))
            path = next((p for p in candidates if p.is_file()), candidates[0])
            if not path.is_relative_to(wav_root):
                raise ValueError(f"WAV outside WAV root: {name!r} in {source}")
            relative = path.relative_to(wav_root).as_posix()
            if relative in records:
                raise ValueError(f"Duplicate WAV {relative!r}: "
                                 f"{sources[relative]} and {source}")
            records[relative] = record
            sources[relative] = str(source)
    if not records:
        raise ValueError("No WAV records in annotation JSON(s)")
    return dict(sorted(records.items()))


def fingerprint_inputs(records: dict[str, dict], wav_root: Path) -> dict[str, dict]:
    """Hash audio bytes and all annotation data so cache entries cannot go stale."""
    result = {}
    for name, record in records.items():
        wav = wav_root / name
        result[name] = {
            "wav_sha256": sha256_file(wav) if wav.is_file() else None,
            "annotation_sha256": stable_hash(record),
        }
    return result


def write_manifest(records: dict[str, dict], wav_root: Path,
                   fingerprints: dict[str, dict], output: Path) -> None:
    """Export a human-readable, read-only inventory of benchmark inputs."""
    import soundfile as sf

    fields = ("wav", "duration_s", "sample_rate_hz", "channels",
              "validated_manual", "manual_not_validated", "automatic_proposals",
              "annotation_exhaustive", "wav_sha256", "annotation_sha256", "error")
    with output.open("w", encoding="utf-8", newline="") as stream:
        writer = csv.DictWriter(stream, fieldnames=fields)
        writer.writeheader()
        for name, record in records.items():
            chirps = [c for c in record.get("chirps", []) if isinstance(c, dict)]
            row = {
                "wav": name,
                "validated_manual": sum(c.get("source") == "manual" and
                                        c.get("status") == "validated" for c in chirps),
                "manual_not_validated": sum(c.get("source") == "manual" and
                                            c.get("status") != "validated" for c in chirps),
                "automatic_proposals": sum(c.get("source") == "automatic"
                                           for c in chirps),
                # Optional future metadata, never infer completeness from a count.
                "annotation_exhaustive": record.get("annotation_exhaustive", ""),
                **fingerprints[name],
            }
            try:
                info = sf.info(wav_root / name)
                row.update(duration_s=round(info.duration, 6),
                           sample_rate_hz=info.samplerate, channels=info.channels)
            except (RuntimeError, OSError, ValueError) as exc:
                row["error"] = f"{type(exc).__name__}: {exc}"
            writer.writerow(row)
