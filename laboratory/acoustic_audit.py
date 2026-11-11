"""Acoustic support audit of manual chirps using the analyser's actual STFT/Reassignment blend.

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

import numpy as np
import soundfile as sf

DEFAULT_ANALYSER_ROOT = Path(__file__).resolve().parents[2] / "Bat-Acoustic-Analyser"
FIELDS = ("partition", "session", "wav", "annotation", "chirp_id",
          "duration_ms", "snr_median_db", "snr_max_db", "snr_p10_db",
          "ridge_offset_median_khz", "ridge_offset_p90_khz",
          "support_fraction_3khz", "samples_evaluated", "review_priority", "error")


def load_display_pipeline(analyser_root: Path):
    root = analyser_root.resolve()
    if not (root / "bat_analyser" / "spectrogram_pipeline.py").is_file():
        raise ValueError(f"Cannot find bat_analyser/spectrogram_pipeline.py in {root}")
    # Use the display implementation, not a second imitation of its blend.
    if str(root) not in sys.path:
        sys.path.insert(0, str(root))
    from bat_analyser.spectrogram_pipeline import compute_display_representation
    return compute_display_representation


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


def evaluate_chirp(wav_path: Path, chirp: dict, display_pipeline,
                   *, fft_size: int = 1024, overlap_fraction: float = 0.75,
                   reassignment_percent: int = 35, spectral_alpha: float = 0.0,
                   max_columns: int = 1600, samples: int = 20,
                   margin_ms: float = 15.0, band_khz: float = 12.0) -> dict:
    """Measure local support *on the same rendered 65/35 display layer*.

    This is a diagnostic display-based score, not an independent SNR or a
    validation against the underlying acoustic truth. Neighboring harmonics
    and calls can dominate the local blended spectral ridge.
    """
    if not 0 <= reassignment_percent <= 100:
        raise ValueError("reassignment_percent must be between 0 and 100")
    if fft_size < 16 or not 0 <= overlap_fraction < 1 or max_columns < 2:
        raise ValueError("invalid display resolution settings")
    if not 0 <= spectral_alpha <= 4:
        raise ValueError("spectral_alpha must be 0..4")
    if samples < 3 or margin_ms < 4 or band_khz < 3:
        raise ValueError("invalid sampling/margin/band configuration")

    t_ms, f_khz = points_of(chirp)
    with sf.SoundFile(str(wav_path)) as sound:
        sr = sound.samplerate
        duration_ms = len(sound) * 1000.0 / sr
        if t_ms[0] < 0 or t_ms[-1] > duration_ms:
            raise ValueError("trajectory outside WAV")
        start = max(0, int((t_ms[0] - margin_ms) * sr / 1000))
        stop = min(len(sound), int(math.ceil((t_ms[-1] + margin_ms) * sr / 1000)))
        if stop - start < max(fft_size, 512) + 1:
            raise ValueError("excerpt too short for analysis")
        sound.seek(start)
        audio = sound.read(stop - start, dtype="float32", always_2d=True)
    # The desktop GUI also downmixes channels by arithmetic mean.
    mono = audio.mean(axis=1).astype(np.float32)
    result = display_pipeline(
        lambda offset, count: mono[offset:offset+count],
        len(mono), sr, fft_size=fft_size,
        overlap_fraction=overlap_fraction,
        start_time=start / sr, max_columns=max_columns,
        spectral_alpha=spectral_alpha, representation="mixed",
        reassignment_percent=reassignment_percent)
    freq = np.asarray(result.frequencies_hz, dtype=float) / 1000.0
    times = np.asarray(result.times_s, dtype=float) * 1000.0
    db = np.asarray(result.db, dtype=float)
    if db.shape != (len(freq), len(times)) or not np.isfinite(db).all():
        raise ValueError("invalid blended display grid")
    if not len(freq) or not len(times):
        raise ValueError("empty time/frequency grid")
    # Estimate noise on the blended representation itself, outside chirp
    # (avoid the 2 ms neighborhood of both endpoints).
    off = ((times < t_ms[0] - 2.0) | (times > t_ms[-1] + 2.0))
    if int(off.sum()) < 6:
        raise ValueError("not enough off-chirp frames for noise estimate")
    noise_db = np.median(db[:, off], axis=1)

    target_times = np.linspace(t_ms[0], t_ms[-1], samples)
    expected_freq = np.interp(target_times, t_ms, f_khz)
    frame_indices = np.abs(times[:, None] - target_times[None, :]).argmin(axis=0)
    snrs, offsets, supported = [], [], []
    for index, (ti, frequency) in enumerate(zip(frame_indices, expected_freq)):
        if target_times[index] < times[0] or target_times[index] > times[-1]:
            raise ValueError("reference outside blended spectrogram grid")
        neighborhood = np.flatnonzero(np.abs(freq - frequency) <= band_khz)
        central = np.flatnonzero(np.abs(freq - frequency) <= 3.0)
        if not len(neighborhood) or not len(central):
            raise ValueError("reference frequency not covered by blended display grid")
        # Local signal/noise both use the GUI's linear-magnitude mix (in dB
        # after blending). This SNR proxy should NOT be compared to the
        # detector's STFT-only SNR classification.
        signal_db = float(np.max(db[central, ti]))
        background_db = float(np.median(noise_db[central]))
        snrs.append(signal_db - background_db)
        a, b = max(0, ti - 1), min(len(times), ti + 2)
        # Compare relative magnitudes rather than adding dB directly.
        local_db = db[np.ix_(neighborhood, np.arange(a, b))]
        local_amp = np.power(10.0, np.clip(local_db, -120, 120) / 20.0)
        profile = local_amp.sum(axis=1)
        if not np.any(profile > 1e-6):
            offsets.append(float("nan"))
            supported.append(False)
            continue
        ridge_khz = float(freq[neighborhood[np.argmax(profile)]])
        error_khz = abs(ridge_khz - frequency)
        offsets.append(error_khz)
        supported.append(error_khz <= 3.0)

    finite = np.asarray([x for x in offsets if math.isfinite(x)])
    snr_array = np.asarray(snrs)
    row = {
        "duration_ms": float(t_ms[-1] - t_ms[0]),
        "snr_median_db": round(float(np.median(snr_array)), 2),
        "snr_max_db": round(float(np.max(snr_array)), 2),
        "snr_p10_db": round(float(np.percentile(snr_array, 10)), 2),
        "ridge_offset_median_khz": round(float(np.median(finite)), 3) if len(finite) else "",
        "ridge_offset_p90_khz": round(float(np.percentile(finite, 90)), 3) if len(finite) else "",
        "support_fraction_3khz": round(float(np.mean(supported)), 3),
        "samples_evaluated": int(samples), "error": "",
    }
    # Display-derived thresholds are provisional, not the established
    # high-SNR detection thresholds of the benchmark.
    if (row["snr_median_db"] >= 15 and row["support_fraction_3khz"] >= 0.7
            and row["ridge_offset_median_khz"] != ""
            and row["ridge_offset_median_khz"] <= 3):
        row["review_priority"] = "strong_support_unverified"
    elif row["snr_median_db"] < 6 or row["support_fraction_3khz"] < 0.3:
        row["review_priority"] = "review_first"
    else:
        row["review_priority"] = "moderate_support_unverified"
    return row


def run(dataset: Path, output: Path, *, analyser_root: Path = DEFAULT_ANALYSER_ROOT,
        max_chirps: int | None = None, fft_size: int = 1024,
        overlap_fraction: float = 0.75, reassignment_percent: int = 35,
        spectral_alpha: float = 0.0, max_columns: int = 1600) -> dict:
    """Calibration-only by construction: test entries are never opened."""
    dataset, output = dataset.resolve(), output.resolve()
    if output == dataset or output.is_relative_to(dataset):
        raise ValueError("output must be outside dataset")
    if max_chirps is not None and max_chirps < 1:
        raise ValueError("max_chirps must be >= 1")
    if not 0 <= reassignment_percent <= 100:
        raise ValueError("reassignment_percent must be in 0..100")
    manifest = dataset/"manifest.csv"
    with manifest.open("r", encoding="utf-8-sig", newline="") as file:
        rows = list(csv.DictReader(file))
    if not rows:
        raise ValueError("empty manifest")
    display_pipeline = load_display_pipeline(analyser_root)
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
                row.update(evaluate_chirp(wav, chirp, display_pipeline,
                                          fft_size=fft_size,
                                          overlap_fraction=overlap_fraction,
                                          reassignment_percent=reassignment_percent,
                                          spectral_alpha=spectral_alpha,
                                          max_columns=max_columns))
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
    summary = {"dataset":str(dataset), "analyser_root":str(analyser_root.resolve()),
               "representation":"Bat-Acoustic-Analyser mixed display",
               "partition":"calibration", "stft_fft_size":fft_size,
               "scoring_protocol":"vertical_legacy",
               "error_units":"kHz_vertical",
               "geometry_note":"V1 remains a vertical-frequency diagnostic, not square-normal geometry.",
               "stft_overlap_fraction":overlap_fraction,
               "reassignment_fft_size":512, "reassignment_percent":reassignment_percent,
               "stft_percent":100-reassignment_percent,
               "spectral_alpha":spectral_alpha,
               "max_columns":max_columns,
               "chirps_processed":len(results),
               "counts_by_review_priority":dict(Counter(r["review_priority"] for r in results)),
               "errors":len(errors),
               "note":"Display-derived local contrast and blended-ridge support; no automated validity verdict.",
               "limitations":"Changing zoom, FFT, overlap or spectral subtraction can change the GUI display. Other calls/harmonics may dominate a local ridge."}
    (output/"acoustic_summary.json").write_text(json.dumps(summary,ensure_ascii=False,indent=2)+"\n",encoding="utf-8")
    (output/"acoustic_errors.json").write_text(json.dumps(errors,ensure_ascii=False,indent=2)+"\n",encoding="utf-8")
    return summary


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--dataset",type=Path,required=True)
    parser.add_argument("--output",type=Path,required=True)
    parser.add_argument("--analyser-root",type=Path,default=DEFAULT_ANALYSER_ROOT,
                        help="Checkout of Bat-Acoustic-Analyser used for your annotations")
    parser.add_argument("--fft-size",type=int,default=1024)
    parser.add_argument("--overlap",type=float,default=0.75)
    parser.add_argument("--reassignment-percent",type=int,default=35)
    parser.add_argument("--spectral-alpha",type=float,default=0.0,
                        help="0 disables subtraction; e.g. 1.7 if enabled in the GUI")
    parser.add_argument("--max-columns",type=int,default=1600)
    parser.add_argument("--max-chirps",type=int)
    args = parser.parse_args(argv)
    try:
        summary = run(args.dataset,args.output,analyser_root=args.analyser_root,
                      max_chirps=args.max_chirps,fft_size=args.fft_size,
                      overlap_fraction=args.overlap,
                      reassignment_percent=args.reassignment_percent,
                      spectral_alpha=args.spectral_alpha,
                      max_columns=args.max_columns)
    except (OSError,ValueError,ImportError) as exc:
        parser.exit(2,f"Error: {exc}\n")
    print(json.dumps(summary,indent=2,ensure_ascii=False))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
