"""Shared, zoom-independent square time/frequency geometry for bat chirps.

Times are milliseconds, frequencies are kHz, distances are kHz equivalents.
The manual annotation alone determines the time scale; predictions never do.
"""
from __future__ import annotations

import numpy as np

PROTOCOL = "square_normals_v1"


def square_time_scale(annotation_t_ms, annotation_f_khz, *,
                      near_horizontal_limit_khz_per_ms=1.0,
                      fallback_khz_per_ms=10.0):
    """Convert time into frequency-equivalent units using the manual extent."""
    t = np.asarray(annotation_t_ms, dtype=float)
    f = np.asarray(annotation_f_khz, dtype=float)
    if (t.ndim != 1 or f.ndim != 1 or len(t) < 2
            or t.shape != f.shape or not np.all(np.isfinite(t))
            or not np.all(np.isfinite(f)) or np.any(np.diff(t) <= 0)):
        raise ValueError("invalid manual chirp for square calibration")
    if (not np.isfinite(near_horizontal_limit_khz_per_ms)
            or near_horizontal_limit_khz_per_ms <= 0
            or not np.isfinite(fallback_khz_per_ms)
            or fallback_khz_per_ms <= 0):
        raise ValueError("invalid square calibration fallback")
    scale = float(np.ptp(f) / (t[-1] - t[0]))
    if not np.isfinite(scale):
        raise ValueError("invalid square calibration time scale")
    if scale < near_horizontal_limit_khz_per_ms:
        return float(fallback_khz_per_ms), "near_horizontal_fallback"
    return scale, "square"


def reference_normals(annotation_t_ms, annotation_f_khz, sample_t_ms):
    """Manual f(t) and unit normals (u, f) at requested reference times.

    Normals always point to the positive-frequency side, including steep
    slopes; a single manual-derived scale is used for the whole chirp.
    """
    t = np.asarray(annotation_t_ms, dtype=float)
    f = np.asarray(annotation_f_khz, dtype=float)
    scale, mode = square_time_scale(t, f)
    x = np.asarray(sample_t_ms, dtype=float)
    if x.ndim != 1 or not np.all(np.isfinite(x)) or np.any(x < t[0]) or np.any(x > t[-1]):
        raise ValueError("reference samples outside manual annotation")
    slopes = np.diff(f) / np.diff(t)
    segments = np.clip(np.searchsorted(t[1:], x, side="right"), 0, len(slopes)-1)
    local_slope = slopes[segments]
    norm = np.hypot(scale, local_slope)
    return {
        "reference_freqs_khz": np.interp(x, t, f),
        "normal_u": -local_slope / norm,
        "normal_f": scale / norm,
        "time_scale_khz_per_ms": scale,
        "normalization_mode": mode,
    }


def compare_normal_curves(annotation_t_ms, annotation_f_khz,
                          predicted_t_ms, predicted_f_khz, sample_t_ms,
                          *, max_radius_khz_eq=None):
    """Intersect manual normals with prediction segments, in square units.

    Intersections are assigned in nondecreasing prediction-polyline order;
    non-intersected manual samples are uncovered (NaN), NOT extrapolated.
    A short prediction therefore receives an explicit coverage penalty.
    """
    t = np.asarray(annotation_t_ms, dtype=float)
    f = np.asarray(annotation_f_khz, dtype=float)
    pt = np.asarray(predicted_t_ms, dtype=float)
    pf = np.asarray(predicted_f_khz, dtype=float)
    x = np.asarray(sample_t_ms, dtype=float)
    ref = reference_normals(t, f, x)
    if (pt.ndim != 1 or pf.ndim != 1 or len(pt) < 2 or pt.shape != pf.shape
            or not np.isfinite(pt).all() or not np.isfinite(pf).all()
            or np.any(np.diff(pt) <= 0)):
        raise ValueError("invalid predicted trajectory")
    if max_radius_khz_eq is not None and (not np.isfinite(max_radius_khz_eq)
                                          or max_radius_khz_eq <= 0):
        raise ValueError("invalid normal radius")
    scale = ref["time_scale_khz_per_ms"]
    pu = (pt - t[0]) * scale
    p0u, p1u = pu[:-1], pu[1:]
    p0f, p1f = pf[:-1], pf[1:]
    du, df = p1u - p0u, p1f - p0f
    signed = np.full(len(x), np.nan)
    hit_t = np.full(len(x), np.nan)
    hit_f = np.full(len(x), np.nan)
    previous_progress = -np.inf
    for i, (ts, fs, nu, nf) in enumerate(zip(x, ref["reference_freqs_khz"],
                                           ref["normal_u"], ref["normal_f"])):
        mu = (ts - t[0]) * scale
        # Tangent = (nf,-nu), perpendicular to the normal (nu,nf).
        a = (p0u - mu) * nf - (p0f - fs) * nu
        delta = du * nf - df * nu
        with np.errstate(divide="ignore", invalid="ignore"):
            alpha = -a / delta
        # A segment entirely on the normal: select its closest point.
        coincident = (np.abs(delta) < 1e-10) & (np.abs(a) < 1e-10)
        denom = du * du + df * df
        closest = np.clip(((mu - p0u) * du + (fs - p0f) * df)
                          / np.maximum(denom, 1e-20), 0, 1)
        alpha = np.where(coincident, closest, alpha)
        possible = (np.isfinite(alpha) & (alpha >= -1e-9)
                    & (alpha <= 1 + 1e-9))
        alpha = np.clip(alpha, 0, 1)
        progress = np.arange(len(du)) + alpha
        possible &= progress >= previous_progress - 1e-9
        dist = ((p0u + alpha * du - mu) * nu
                + (p0f + alpha * df - fs) * nf)
        if max_radius_khz_eq is not None:
            possible &= np.abs(dist) <= max_radius_khz_eq
        if not np.any(possible):
            continue
        choices = np.flatnonzero(possible)
        chosen = choices[np.argmin(np.abs(dist[choices]))]
        signed[i] = float(dist[chosen])
        hit_t[i] = float(pt[chosen] + alpha[chosen] * (pt[chosen+1]-pt[chosen]))
        hit_f[i] = float(p0f[chosen] + alpha[chosen] * df[chosen])
        previous_progress = float(progress[chosen])
    covered = np.isfinite(signed)
    return {
        **ref,
        "reference_times_ms": x,
        "signed_normal_khz_eq": signed,
        "abs_normal_khz_eq": np.abs(signed),
        "covered": covered,
        "coverage_fraction": float(np.mean(covered)),
        "matched_times_ms": hit_t,
        "matched_freqs_khz": hit_f,
        "protocol": PROTOCOL,
    }
