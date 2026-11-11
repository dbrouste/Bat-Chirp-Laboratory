"""Acoustic support audit of *manual validated* chirps using Reassignment Flattop 512.

Diagnostic only: it must not certify a reference, alter JSON, or tune on test.
Processes short, bounded WAV excerpts, not entire multi-minute recordings.
"""
from __future__ import annotations

import argparse
import csv
import json
import math
import sys
from collections import Counter, defaultdict
from pathlib import Path
from statistics import median

import numpy as np
import soundfile as sf

DEFAULT_PROCESSING_ROOT = Path(__file__).resolve().parents[2] / "bat_full_spectrum_processing"
FIELDS = ("partition", "session", "wav", "annotation", "chirp_id",
          "duration_ms", "snr_median_db", "snr_max_db", "snr_p10_db",
          "ridge_offset_median_khz", "ridge_offset_p90_khz",
          "support_fraction_3khz", "samples_evaluated", "review_priority", "error")


def load_reassignment(processing_root: Path):
    root = processing_root.resolve()
    if not (root / "bat_analysis" / "reassignment.py").is_file():
        raise ValueError(f"Cannot find bat_analysis/reassignment.py in {root}")
    # Only import from the exact user-selected checkout.
    if str(root) not in sys.path:
        sys.path.insert(0, str(root))
    from bat_analysis.reassignment import reassigned_spectrogram
    return reassigned_spectrogram


def points_of(chirp: dict) -> tuple[np.ndarray, np.ndarray]:
    raw = chirp.get("points", [])
    if not isinstance(raw, list) or len(raw) < 2:
        raise ValueError("less than two trajectory points")
    xy = np.asarray([(float(p["t_ms"]), float(p["f_khz"])) for p in raw], dtype=float)
    if not np.isfinite(xy).all():
        raise ValueError("non-finite trajectory")
    order = np.argsort(xy[:, 0])
    xy = xy[order]
    if np.any(np.diff(xy[:, 0]) <= 0):
        raise ValueError("duplicate/non-increasing time")
    return xy[:, 0], xy[:, 1]


def evaluate_chirp(wav_path: Path, chirp: dict, reassignment,
                   *, n_fft: int = 512, hop: int = 64,
                   samples: int = 20, margin_ms: float = 15.0,
                   band_khz: float = 12.0) -> dict:
    """Return spectral diagnostics relative to the human annotation.

    Local noise floor uses off-chirp frames in an excerpt, separately per
    frequency bin. The returned SNR is a local proxy, not a calibrated physical
    signal-to-noise ratio. Ridge offsets can reflect harmonics or nearby calls.
    """
    t_ms, f_khz = points_of(chirp)
    if samples < 3 or margin_ms < 4 or band_khz < 3:
        raise ValueError("invalid sampling/margin/band configuration")
    with sf.SoundFile(str(wav_path)) as sound:
        sr = sound.samplerate
        duration_ms = len(sound) * 1000.0 / sr
        if t_ms[0] < 0 or t_ms[-1] > duration_ms:
            raise ValueError("trajectory outside WAV")
        start = max(0, int((t_ms[0] - margin_ms) * sr / 1000))
        stop = min(len(sound), int(math.ceil((t_ms[-1] + margin_ms)*sr/1000)))
        if stop - start < n_fft + hop:
            raise ValueError("excerpt too short for analysis")
        sound.seek(start)
        audio = sound.read(stop - start, dtype="float32", always_2d=True)
    # Match the desktop analyzer's channel downmix convention.
    audio = audio.mean(axis=1).astype(np.float64)
    fmin = max(0.0, (float(f_khz.min())-band_khz-5.0)*1000)
    fmax = min(sr/2, (float(f_khz.max())+band_khz+5.0)*1000)
    if fmax <= fmin:
        raise ValueError("reference frequency outside Nyquist band")
    spec = reassignment(audio, sr, n_fft=n_fft, hop=hop, fmin=fmin, fmax=fmax,
                        noise_percentile=50, snr_min_db=4, snr_full_db=16)
    freqs = np.asarray(spec["freqs_hz"])/1000.0
    times = np.asarray(spec["times_s"])*1000.0 + start*1000.0/sr
    reassigned = np.asarray(spec["reassigned_power"], dtype=float)
    stft = np.asarray(spec["stft_power"], dtype=float)
    if not len(freqs) or not len(times):
        raise ValueError("empty time/frequency grid")

    # Local noise estimate uses off-annotation frames, excluding a 2 ms guard.
    off = (times < t_ms[0]-2) | (times > t_ms[-1]+2)
    if off.sum() < 6:
        raise ValueError("not enough off-chirp frames for noise estimate")
    noise_power = np.percentile(stft[:, off], 50, axis=1)
    noise_power = np.maximum(noise_power, 1e-30)
    frame_indices = np.rint(np.interp(
        np.linspace(t_ms[0], t_ms[-1], samples),
        times, np.arange(len(times)))).astype(int)
    target_times = np.linspace(t_ms[0], t_ms[-1], samples)
    expected = np.interp(target_times, t_ms, f_khz)

    snrs, offsets, supported = [], [], []
    for ti, frequency in zip(frame_indices, expected):
        # Refuse extrapolation outside measured STFT frames.
        if target_times[len(snrs)] < times[0] or target_times[len(snrs)] > times[-1]:
            raise ValueError("reference outside spectrogram grid")
        neighborhood = np.flatnonzero(abs(freqs-frequency) <= band_khz)
        center = np.flatnonzero(abs(freqs-frequency) <= 3.0)
        if not len(neighborhood) or not len(center):
            raise ValueError("reference not covered by frequency grid")
        # Peak regular energy near the manually drawn trajectory.
        signal = float(np.max(stft[center, ti]))
        noise = float(np.median(noise_power[center]))
        snr = 10*np.log10(max(signal, 1e-30) / max(noise, 1e-30))
        snrs.append(float(snr))
        # Reassigned ridge is sparse. Pool across a small time neighborhood
        # before locating the closest dominant peak in a limited frequency band.
        ta, tb = max(0, ti-1), min(reassigned.shape[1], ti+2)
        local = reassigned[neighborhood, ta:tb].sum(axis=1)
        if not np.any(local > 0):
            offsets.append(float("nan"))
            supported.append(False)
            continue
        ridge_khz = float(freqs[neighborhood[np.argmax(local)]])
        error = abs(ridge_khz-frequency)
        offsets.append(error)
        supported.append(error <= 3.0)
    finite = np.asarray([x for x in offsets if math.isfinite(x)])
    snr_array = np.asarray(snrs)
    row = {
        "duration_ms": float(t_ms[-1]-t_ms[0]),
        "snr_median_db": round(float(np.median(snr_array)), 2),
        "snr_max_db": round(float(np.max(snr_array)), 2),
        "snr_p10_db": round(float(np.percentile(snr_array, 10)), 2),
        "ridge_offset_median_khz": round(float(np.median(finite)), 3) if len(finite) else "",
        "ridge_offset_p90_khz": round(float(np.percentile(finite, 90)), 3) if len(finite) else "",
        "support_fraction_3khz": round(float(np.mean(supported)), 3),
        "samples_evaluated": int(samples),
        "error": "",
    }
    # Heuristic triage only; not an accuracy calibration or expert validation.
    if (row["snr_median_db"] >= 15 and row["support_fraction_3khz"] >= 0.7
            and row["ridge_offset_median_khz"] != ""
            and row["ridge_offset_median_khz"] <= 3):
        row["review_priority"] = "strong_support_unverified"
    elif row["snr_median_db"] < 6 or row["support_fraction_3khz"] < 0.3:
        row["review_priority"] = "review_first"
    else:
        row["review_priority"] = "moderate_support_unverified"
    return row


def run(dataset: Path, output: Path, *, processing_root: Path = DEFAULT_PROCESSING_ROOT,
        max_chirps: int | None = None, n_fft: int = 512, hop: int = 64) -> dict:
    """Calibration-only by construction: test entries are never opened."""
    dataset, output = dataset.resolve(), output.resolve()
    if output == dataset or output.is_relative_to(dataset):
        raise ValueError("output must be outside dataset")
    if max_chirps is not None and max_chirps < 1:
        raise ValueError("max_chirps must be >= 1")
    if n_fft != 512 or hop != 64:
        raise ValueError("currently supported reference method: Flattop 512 / hop 64")
    manifest = dataset/"manifest.csv"
    with manifest.open("r", encoding="utf-8-sig", newline="") as file:
        rows = list(csv.DictReader(file))
    if not rows:
        raise ValueError("empty manifest")
    reassignment = load_reassignment(processing_root)
    output.mkdir(parents=True, exist_ok=True)
    results = []
    errors = []
    json_cache = {}
    for entry in rows:
        if entry["partition"] != "calibration":
            continue
        wav = (dataset/entry["dataset_wav"]).resolve()
        annotation = (dataset/entry["dataset_json"]).resolve()
        if not wav.is_relative_to(dataset) or not annotation.is_relative_to(dataset):
            raise ValueError("manifest path outside dataset")
        try:
            if annotation not in json_cache:
                json_cache[annotation] = json.loads(annotation.read_text(encoding="utf-8-sig"))
            record = json_cache[annotation]["files"][wav.name]
            chirps = record["chirps"]
            if not isinstance(chirps, list):
                raise ValueError("chirps not a list")
        except (OSError, ValueError, KeyError, TypeError) as exc:
            errors.append({"wav":entry["dataset_wav"],"error":str(exc)})
            continue
        for chirp in chirps:
            if not isinstance(chirp, dict) or chirp.get("source") != "manual" or chirp.get("status") != "validated":
                continue
            if max_chirps is not None and len(results) >= max_chirps:
                break
            row = {key: "" for key in FIELDS}
            row.update({k:entry[k] for k in ("partition","session")})
            row.update(wav=entry["dataset_wav"],annotation=entry["dataset_json"],
                       chirp_id=chirp.get("chirp_id",""))
            try:
                row.update(evaluate_chirp(wav, chirp, reassignment,
                                          n_fft=n_fft, hop=hop))
            except (OSError, ValueError, TypeError, IndexError, RuntimeError) as exc:
                row["error"] = str(exc)
                row["review_priority"] = "analysis_error"
                errors.append({"wav":entry["dataset_wav"],
                               "chirp_id":row["chirp_id"],"error":str(exc)})
            results.append(row)
        if max_chirps is not None and len(results) >= max_chirps:
            break
    if not results:
        raise ValueError("no manual validated chirps in calibration")
    with (output/"acoustic_chirps.csv").open("w", encoding="utf-8-sig", newline="") as file:
        writer = csv.DictWriter(file, fieldnames=FIELDS)
        writer.writeheader()
        writer.writerows(results)
    summary = {"dataset":str(dataset), "processing_root":str(processing_root.resolve()),
               "partition":"calibration", "n_fft":n_fft, "hop":hop,
               "chirps_processed":len(results),
               "counts_by_review_priority":dict(Counter(r["review_priority"] for r in results)),
               "errors":len(errors),
               "note":"Local SNR proxy and reassigned ridge support; no automated validity verdict.",
               "limitations":"Other calls/harmonics may dominate local ridges. Manual confirmation remains necessary."}
    (output/"acoustic_summary.json").write_text(json.dumps(summary,ensure_ascii=False,indent=2)+"\n",encoding="utf-8")
    (output/"acoustic_errors.json").write_text(json.dumps(errors,ensure_ascii=False,indent=2)+"\n",encoding="utf-8")
    return summary


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--dataset",type=Path,required=True)
    parser.add_argument("--output",type=Path,required=True)
    parser.add_argument("--processing-root",type=Path,default=DEFAULT_PROCESSING_ROOT)
    parser.add_argument("--max-chirps",type=int)
    args = parser.parse_args(argv)
    try:
        summary = run(args.dataset,args.output,processing_root=args.processing_root,
                      max_chirps=args.max_chirps)
    except (OSError,ValueError,ImportError) as exc:
        parser.exit(2,f"Error: {exc}\n")
    print(json.dumps(summary,indent=2,ensure_ascii=False))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
