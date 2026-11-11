"""Build a reproducible local WAV/JSON benchmark corpus (no external dependencies).

The source JSON format is Bat-Acoustic-Analyser's directory-level mapping:
{"files": {"recording.wav": {"chirps": [...]}, ...}}.
Every accepted JSON is copied byte-for-byte, alongside *all* WAVs it references.
Only JSONs containing at least one validated manual chirp are selected by default.
"""

from __future__ import annotations

import argparse
import csv
import hashlib
import json
from collections import Counter, defaultdict
from dataclasses import dataclass
from pathlib import Path, PurePosixPath, PureWindowsPath
import shutil


@dataclass(frozen=True)
class Pair:
    wav: Path
    json: Path
    relative_wav: Path
    relative_json: Path
    session: str
    counts: tuple[int, int, int]
    wav_sha256: str
    json_sha256: str


def sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as source:
        for block in iter(lambda: source.read(1024 * 1024), b""):
            digest.update(block)
    return digest.hexdigest()


def chirp_counts(entry: dict) -> tuple[int, int, int]:
    chirps = entry.get("chirps", [])
    if not isinstance(chirps, list):
        raise ValueError("chirps must be a list")
    manual = sum(isinstance(c, dict) and c.get("source") == "manual"
                 and c.get("status") == "validated" for c in chirps)
    pending = sum(isinstance(c, dict) and c.get("source") == "manual"
                  and c.get("status") != "validated" for c in chirps)
    automatic = sum(isinstance(c, dict) and c.get("source") == "automatic"
                    for c in chirps)
    return manual, pending, automatic


def wav_path_for_entry(folder: Path, name: str, root: Path) -> Path:
    """Reject path escapes and avoid guessing among similarly named files."""
    if (not name or PureWindowsPath(name).is_absolute()
            or PureWindowsPath(name).drive or PurePosixPath(name).is_absolute()):
        raise ValueError(f"unsafe WAV name: {name!r}")
    path = (folder / Path(name.replace("\\", "/"))).resolve()
    if not path.is_relative_to(root):
        raise ValueError(f"WAV outside source root: {name!r}")
    if path.suffix.lower() != ".wav":
        raise ValueError(f"not a WAV name: {name!r}")
    if not path.is_file():
        # Common on case-sensitive hosts when referring to a capitalized .WAV.
        if path.parent.is_dir():
            matches = [p for p in path.parent.iterdir()
                       if p.is_file() and p.name.casefold() == path.name.casefold()]
            if len(matches) == 1:
                path = matches[0].resolve()
        if not path.is_file():
            raise FileNotFoundError(f"missing WAV: {name!r}")
    return path


def scan_source(root: Path) -> tuple[list[Pair], list[tuple[str, str]], dict]:
    """Validate JSONs as indivisible units: never copy incomplete references."""
    warnings: list[tuple[str, str]] = []
    pairs: list[Pair] = []
    claimed: set[Path] = set()
    hashes: dict[Path, str] = {}
    json_files = sorted((p for p in root.rglob("*") if p.suffix.lower() == ".json"),
                        key=lambda p: p.as_posix().casefold())
    wav_count = sum(p.suffix.lower() == ".wav" for p in root.rglob("*") if p.is_file())
    accepted_json = 0
    for path in json_files:
        try:
            data = json.loads(path.read_text(encoding="utf-8-sig"))
        except (OSError, UnicodeError, json.JSONDecodeError) as exc:
            warnings.append((str(path), f"invalid JSON: {exc}"))
            continue
        if not isinstance(data, dict) or not isinstance(data.get("files"), dict):
            # Other JSONs (metadata/settings) are not annotation JSONs.
            continue
        if not data["files"]:
            warnings.append((str(path), "empty files mapping"))
            continue
        batch = []
        try:
            for name, entry in sorted(data["files"].items()):
                if not isinstance(name, str) or not isinstance(entry, dict):
                    raise ValueError("invalid files mapping entry")
                wav = wav_path_for_entry(path.parent, name, root)
                counts = chirp_counts(entry)
                batch.append((wav, counts))
            paths = [item[0] for item in batch]
            if len(set(paths)) != len(paths):
                raise ValueError("duplicate WAV entries inside JSON")
            if any(p in claimed for p in paths):
                raise ValueError("WAV already referenced by another annotation JSON")
        except (ValueError, FileNotFoundError, OSError) as exc:
            warnings.append((str(path), str(exc)))
            continue
        if not any(counts[0] for _, counts in batch):
            warnings.append((str(path), "no validated manual chirps"))
            continue
        json_hash = sha256_file(path)
        rel_json = path.relative_to(root)
        # A folder is a recording session; never split its JSONs across sets.
        session = path.parent.relative_to(root).as_posix()
        for wav, counts in batch:
            if wav not in hashes:
                hashes[wav] = sha256_file(wav)
            pairs.append(Pair(wav, path, wav.relative_to(root), rel_json,
                              session, counts, hashes[wav], json_hash))
            claimed.add(wav)
        accepted_json += 1
    duplicate_wavs = sum(n - 1 for n in Counter(p.wav_sha256 for p in pairs).values()
                         if n > 1)
    stats = {"json_scanned": len(json_files), "wav_scanned": wav_count,
             "json_accepted": accepted_json, "wav_accepted": len(pairs),
             "manual_chirps": sum(p.counts[0] for p in pairs),
             "duplicate_audio_files": duplicate_wavs, "warnings": len(warnings)}
    return pairs, warnings, stats


def partition_by_session(pairs: list[Pair], test_ratio: float,
                         seed: int) -> dict[str, str]:
    """Hold out whole sessions; collapse identical-audio sessions first."""
    sessions = {p.session for p in pairs}
    parent = {s: s for s in sessions}

    def find(s: str) -> str:
        while parent[s] != s:
            parent[s] = parent[parent[s]]
            s = parent[s]
        return s

    def union(a: str, b: str) -> None:
        x, y = find(a), find(b)
        if x != y:
            parent[max(x, y)] = min(x, y)

    hash_first: dict[str, str] = {}
    for pair in pairs:
        if pair.wav_sha256 in hash_first:
            union(pair.session, hash_first[pair.wav_sha256])
        else:
            hash_first[pair.wav_sha256] = pair.session
    grouped: dict[str, list[Pair]] = defaultdict(list)
    for pair in pairs:
        grouped[find(pair.session)].append(pair)
    groups = sorted(grouped, key=lambda group: hashlib.sha256(
        f"{seed}:{group}".encode("utf-8")).hexdigest())
    # Group weights are reference-chirp counts, not automatically proposed chirps.
    weights = {group: sum(p.counts[0] for p in grouped[group]) for group in groups}
    target = sum(weights.values()) * test_ratio
    test_groups: set[str] = set()
    test_weight = 0
    for group in groups[:-1]:  # keep at least one group in calibration
        if test_weight >= target:
            break
        test_groups.add(group)
        test_weight += weights[group]
    return {session: ("test" if find(session) in test_groups else "calibration")
            for session in sessions}


def build_dataset(source: Path, output: Path, *, test_ratio: float = .2,
                  seed: int = 42, dry_run: bool = False) -> dict:
    source, output = source.resolve(), output.resolve()
    if not source.is_dir():
        raise ValueError(f"source directory not found: {source}")
    if not 0 < test_ratio < 1:
        raise ValueError("test_ratio must be strictly between 0 and 1")
    if source.is_relative_to(output) or output.is_relative_to(source):
        raise ValueError("output must be outside source and must not contain source")
    if output.exists() and (not output.is_dir() or any(output.iterdir())) and not dry_run:
        raise ValueError(f"output is not empty: {output} (use a new directory)")
    pairs, warnings, summary = scan_source(source)
    if not pairs:
        raise ValueError("no complete annotated JSON/WAV group with validated manual chirps")
    partitions = partition_by_session(pairs, test_ratio, seed)
    summary.update({"source": str(source), "output": str(output),
                    "test_ratio_requested": test_ratio, "seed": seed,
                    "session_count": len(set(p.session for p in pairs)),
                    "split": {k: {
                        "wav": sum(partitions[p.session] == k for p in pairs),
                        "validated_manual_chirps": sum(p.counts[0] for p in pairs
                                                       if partitions[p.session] == k),
                        "json": len({p.json for p in pairs if partitions[p.session] == k}),
                    } for k in ("calibration", "test")}})
    if summary["split"]["test"]["wav"] == 0:
        warnings.append((str(source), "only one independent group; test set is empty"))
    summary["warnings"] = len(warnings)
    if dry_run:
        return summary

    output.mkdir(parents=True, exist_ok=True)
    fields = ("partition", "session", "source_wav", "source_json", "dataset_wav",
              "dataset_json", "wav_sha256", "json_sha256", "wav_size_bytes",
              "validated_manual", "manual_not_validated", "automatic_proposals")
    try:
        with (output / "manifest.csv").open("w", encoding="utf-8-sig", newline="") as fh:
            writer = csv.DictWriter(fh, fieldnames=fields)
            writer.writeheader()
            copied_json: set[Path] = set()
            copied_wav: set[Path] = set()
            for pair in sorted(pairs, key=lambda p: p.relative_wav.as_posix()):
                split = partitions[pair.session]
                wav_dest = Path(split) / "wav" / pair.relative_wav
                json_dest = Path(split) / "annotations" / pair.relative_json
                if wav_dest not in copied_wav:
                    (output / wav_dest).parent.mkdir(parents=True, exist_ok=True)
                    shutil.copy2(pair.wav, output / wav_dest)
                    if sha256_file(output / wav_dest) != pair.wav_sha256:
                        raise OSError(f"WAV copy verification failed: {pair.wav}")
                    copied_wav.add(wav_dest)
                if json_dest not in copied_json:
                    (output / json_dest).parent.mkdir(parents=True, exist_ok=True)
                    shutil.copy2(pair.json, output / json_dest)
                    if sha256_file(output / json_dest) != pair.json_sha256:
                        raise OSError(f"JSON copy verification failed: {pair.json}")
                    copied_json.add(json_dest)
                writer.writerow({"partition": split, "session": pair.session,
                                 "source_wav": str(pair.wav), "source_json": str(pair.json),
                                 "dataset_wav": wav_dest.as_posix(),
                                 "dataset_json": json_dest.as_posix(),
                                 "wav_sha256": pair.wav_sha256,
                                 "json_sha256": pair.json_sha256,
                                 "wav_size_bytes": pair.wav.stat().st_size,
                                 "validated_manual": pair.counts[0],
                                 "manual_not_validated": pair.counts[1],
                                 "automatic_proposals": pair.counts[2]})
        with (output / "warnings.csv").open("w", encoding="utf-8-sig", newline="") as fh:
            writer = csv.writer(fh)
            writer.writerow(["path", "reason"])
            writer.writerows(warnings)
        (output / "summary.json").write_text(json.dumps(summary, indent=2,
                                                 ensure_ascii=False) + "\n",
                                               encoding="utf-8")
    except Exception:
        # Never advertise a partially populated dataset as a finished corpus.
        (output / "summary.json").unlink(missing_ok=True)
        raise
    return summary


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--source", type=Path, required=True,
                        help="Root containing WAV directories and their JSON files")
    parser.add_argument("--output", type=Path, required=True,
                        help="New or empty destination directory, outside source")
    parser.add_argument("--test-ratio", type=float, default=.2)
    parser.add_argument("--seed", type=int, default=42)
    parser.add_argument("--dry-run", action="store_true")
    args = parser.parse_args(argv)
    try:
        summary = build_dataset(args.source, args.output, test_ratio=args.test_ratio,
                                seed=args.seed, dry_run=args.dry_run)
    except (ValueError, OSError) as exc:
        parser.exit(2, f"Error: {exc}\n")
    print(json.dumps(summary, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
