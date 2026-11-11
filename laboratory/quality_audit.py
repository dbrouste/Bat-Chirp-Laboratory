"""Read-only quality audit of validated manual chirps, using a dataset manifest.

Flags are review hints, not automatic rejections or a geometric accuracy score.
Calibration is the only partition analyzed by default.
"""
from __future__ import annotations

import argparse
from collections import Counter, defaultdict
import csv
import json
import math
from pathlib import Path
import statistics
import wave


DEFAULT_LIMITS = {
    "min_duration_ms": 0.5, "max_duration_ms": 20.0,
    "min_freq_khz": 10.0, "max_freq_khz": 180.0,
    "max_points": 20, "max_local_slope_khz_ms": 100.0,
    "max_turns": 2, "max_step_khz": 35.0,
    "turn_slope_epsilon": 0.5,
}


def wav_header(path: Path) -> tuple[float | None, int | None]:
    """Read only the header of conventional PCM WAV files."""
    try:
        with wave.open(str(path), "rb") as handle:
            rate = handle.getframerate()
            if rate > 0:
                return handle.getnframes() / rate, rate
    except (OSError, EOFError, wave.Error):
        pass
    return None, None


def inspect_chirp(chirp: dict, duration_s: float | None,
                  sample_rate: int | None, limits: dict | None = None) -> dict:
    """Inspect one manually validated curve without modifying it."""
    limits = DEFAULT_LIMITS if limits is None else limits
    flags: list[str] = []
    row = dict(chirp_id=chirp.get("chirp_id", ""), points=0, start_ms="",
               end_ms="", duration_ms="", f_min_khz="", f_max_khz="",
               bandwidth_khz="", slope_khz_ms="", max_local_slope_khz_ms="",
               max_step_khz="", direction_changes="", flags="")
    points = chirp.get("points")
    if not isinstance(points, list) or len(points) < 2:
        row["flags"] = "invalid_points"
        return row
    row["points"] = len(points)
    if len(points) > limits["max_points"]:
        flags.append("many_points")
    try:
        coords = [(float(p["t_ms"]), float(p["f_khz"])) for p in points]
    except (KeyError, TypeError, ValueError, OverflowError):
        row["flags"] = "invalid_points"
        return row
    if not all(math.isfinite(t) and math.isfinite(f) for t, f in coords):
        row["flags"] = "nonfinite_points"
        return row
    if any(a[0] >= b[0] for a, b in zip(coords, coords[1:])):
        flags.append("nonincreasing_time")
    coords = sorted(coords)
    times, freqs = zip(*coords)
    dt = times[-1] - times[0]
    row.update(start_ms=times[0], end_ms=times[-1], duration_ms=dt,
               f_min_khz=min(freqs), f_max_khz=max(freqs),
               bandwidth_khz=max(freqs)-min(freqs))
    if dt <= 0:
        flags.append("zero_duration")
    else:
        row["slope_khz_ms"] = (freqs[-1]-freqs[0])/dt
        if dt < limits["min_duration_ms"] or dt > limits["max_duration_ms"]:
            flags.append("unusual_duration")
    if min(freqs) <= 0:
        flags.append("nonpositive_frequency")
    if min(freqs) < limits["min_freq_khz"] or max(freqs) > limits["max_freq_khz"]:
        flags.append("frequency_outside_review_band")
    if duration_s is not None and (times[0] < 0 or times[-1] > 1000*duration_s + 0.01):
        flags.append("outside_wav_duration")
    if sample_rate is not None and max(freqs) > sample_rate/2000 + 0.01:
        flags.append("above_nyquist")
    slopes = [(f1-f0)/(t1-t0) for (t0,f0),(t1,f1) in zip(coords, coords[1:])
              if t1 > t0]
    if slopes:
        maximum = max(abs(slope) for slope in slopes)
        row["max_local_slope_khz_ms"] = maximum
        if maximum > limits["max_local_slope_khz_ms"]:
            flags.append("steep_local_segment")
        signs = [1 if s > 0 else -1 for s in slopes
                 if abs(s) >= limits["turn_slope_epsilon"]]
        row["direction_changes"] = sum(a != b for a,b in zip(signs,signs[1:]))
        if row["direction_changes"] > limits["max_turns"]:
            flags.append("multiple_direction_changes")
    row["max_step_khz"] = max(abs(b-a) for a,b in zip(freqs,freqs[1:]))
    if row["max_step_khz"] > limits["max_step_khz"]:
        flags.append("large_frequency_step")
    row["flags"] = ";".join(dict.fromkeys(flags))
    return row


def audit_dataset(dataset: Path, output: Path, *, include_test: bool = False) -> dict:
    """Build per-chirp and per-session reports without writing into the dataset."""
    dataset = Path(dataset).resolve()
    output = Path(output).resolve()
    if output == dataset or output.is_relative_to(dataset):
        raise ValueError("output must be outside the dataset")
    manifest = dataset / "manifest.csv"
    if not manifest.is_file():
        raise ValueError(f"manifest.csv missing: {manifest}")
    with manifest.open(encoding="utf-8-sig", newline="") as file:
        entries = list(csv.DictReader(file))
    if not entries:
        raise ValueError("empty manifest")

    by_json = defaultdict(list)
    for entry in entries:
        split = entry.get("partition")
        if split not in ("calibration", "test"):
            raise ValueError(f"unexpected partition: {split!r}")
        if split == "test" and not include_test:
            continue
        for field in ("dataset_wav", "dataset_json"):
            path = (dataset / entry[field]).resolve()
            if not path.is_relative_to(dataset):
                raise ValueError(f"manifest path outside dataset: {entry[field]}")
        by_json[entry["dataset_json"]].append(entry)

    results = []
    warnings = []
    for json_name, rows in sorted(by_json.items()):
        annotation_path = dataset / json_name
        try:
            data = json.loads(annotation_path.read_text(encoding="utf-8-sig"))
            files = data["files"]
            if not isinstance(files, dict):
                raise ValueError("'files' is not a mapping")
        except (OSError, KeyError, ValueError, TypeError) as exc:
            warnings.append((str(annotation_path), f"invalid annotation JSON: {exc}"))
            continue
        for entry in rows:
            wav = dataset / entry["dataset_wav"]
            duration_s, rate = wav_header(wav)
            if duration_s is None:
                warnings.append((str(wav), "WAV header unavailable; time and Nyquist checks skipped"))
            wav_entry = files.get(wav.name)
            if not isinstance(wav_entry, dict):
                warnings.append((json_name, f"missing WAV entry: {wav.name}"))
                continue
            chirps = wav_entry.get("chirps", [])
            if not isinstance(chirps, list):
                warnings.append((json_name, f"invalid chirps list: {wav.name}"))
                continue
            for chirp in chirps:
                if not isinstance(chirp, dict) or chirp.get("source") != "manual" or chirp.get("status") != "validated":
                    continue
                row = inspect_chirp(chirp, duration_s, rate)
                row.update(partition=entry["partition"], session=entry["session"],
                           wav=entry["dataset_wav"], annotation=json_name)
                results.append(row)

    fields = ("partition","session","wav","annotation","chirp_id","points",
              "start_ms","end_ms","duration_ms","f_min_khz","f_max_khz",
              "bandwidth_khz","slope_khz_ms","max_local_slope_khz_ms",
              "max_step_khz","direction_changes","flags")
    output.mkdir(parents=True, exist_ok=True)
    with (output/"chirp_quality.csv").open("w",encoding="utf-8-sig",newline="") as file:
        writer = csv.DictWriter(file,fieldnames=fields)
        writer.writeheader()
        writer.writerows(results)
    with (output/"audit_warnings.csv").open("w",encoding="utf-8-sig",newline="") as file:
        writer = csv.writer(file)
        writer.writerow(["path","reason"])
        writer.writerows(warnings)

    per_session = []
    for session in sorted({r["session"] for r in results}):
        subset = [r for r in results if r["session"] == session]
        durations = [r["duration_ms"] for r in subset if isinstance(r["duration_ms"],(int,float))]
        per_session.append({
            "session": session, "chirps": len(subset),
            "flagged": sum(bool(r["flags"]) for r in subset),
            "median_duration_ms": statistics.median(durations) if durations else None,
        })
    counts = Counter(flag for row in results for flag in row["flags"].split(";") if flag)
    summary = {
        "partitions": ["calibration","test"] if include_test else ["calibration"],
        "manual_chirps_inspected": len(results),
        "wav_with_manual_chirps": len({r["wav"] for r in results}),
        "flagged_chirps": sum(bool(r["flags"]) for r in results),
        "flags": dict(sorted(counts.items())),
        "sessions": per_session, "warnings": len(warnings),
        "thresholds_for_review_only": DEFAULT_LIMITS,
        "note": "Flags are heuristic review candidates, not automatic exclusions or measured accuracy.",
    }
    (output/"quality_summary.json").write_text(
        json.dumps(summary,indent=2,ensure_ascii=False)+"\n",encoding="utf-8")
    return summary


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--dataset",type=Path,required=True)
    parser.add_argument("--output",type=Path,required=True)
    parser.add_argument("--include-test",action="store_true",
                        help="Descriptive audit only; do not tune on test data")
    args = parser.parse_args(argv)
    try:
        result = audit_dataset(args.dataset,args.output,include_test=args.include_test)
    except (OSError,ValueError) as exc:
        parser.exit(2,f"Error: {exc}\n")
    print(json.dumps(result,indent=2,ensure_ascii=False))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
