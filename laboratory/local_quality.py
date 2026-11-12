"""Local signed-normal quality metrics on an evenly sampled chirp.

All distances are geometric kHz_eq from the shared square normalization.
Windows are contiguous samples, not independent frequency maxima.
"""
from __future__ import annotations
import numpy as np


def local_error_metrics(signed_offsets, *, window=5):
    """Summarize 3-5 point windows and thirds, retaining precise peak location."""
    offsets=np.asarray(signed_offsets,dtype=float)
    if offsets.ndim!=1 or offsets.size<3 or not np.isfinite(offsets).all():
        raise ValueError("expected at least three finite normal offsets")
    if not isinstance(window,int) or not 3 <= window <= offsets.size:
        raise ValueError("invalid local window")
    n=offsets.size
    windows=np.lib.stride_tricks.sliding_window_view(offsets,window)
    median_abs=np.median(np.abs(windows),axis=1)
    # Signed median captures coherent errors, independent of polarity.
    signed=np.median(windows,axis=1)
    strongest=int(np.argmax(np.abs(signed)))
    strongest_abs=int(np.argmax(median_abs))
    # Use positions of the normalized reference time, partitioned into thirds.
    labels=("start","middle","end")
    by_region={}
    for i,label in enumerate(labels):
        start=(i*n)//3
        stop=((i+1)*n)//3
        subset=offsets[start:stop]
        by_region[label]=float(np.median(np.abs(subset)))
    return {
        "local_window_points":window,
        "local_max_median_abs_khz_eq":float(np.max(median_abs)),
        "local_max_abs_signed_median_khz_eq":float(np.max(np.abs(signed))),
        "local_peak_signed_median_khz_eq":float(signed[strongest]),
        "local_peak_start_index":strongest,
        "local_peak_end_index":strongest+window-1,
        "local_peak_abs_start_index":strongest_abs,
        "local_peak_region":labels[min(2,int(((strongest+window/2)/n)*3))],
        "local_start_median_abs_khz_eq":by_region["start"],
        "local_middle_median_abs_khz_eq":by_region["middle"],
        "local_end_median_abs_khz_eq":by_region["end"],
        "local_signed_window_series_khz_eq":signed,
        "local_abs_window_series_khz_eq":median_abs,
    }


def experimental_local_flag(metrics, *, threshold=1.5):
    """Provisional absolute threshold; not a calibrated false-alarm rate."""
    if threshold<=0:
        raise ValueError("threshold must be >0")
    return bool(metrics["local_max_abs_signed_median_khz_eq"] >= threshold)
