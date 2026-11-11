"""Continuous ridge search across normals to a manually annotated chirp.

Coordinates are calibrated from the manual chirp's complete time and frequency
extents: u = (t_ms-t0_ms) * (frequency_span_khz/time_span_ms), v = f_khz.
Normals and signed lengths are Euclidean in (u,v), expressed as geometric
kHz equivalents, independent of the display zoom or candidate ridge.
No automatic detector is used and manual annotation is never modified.
"""
from __future__ import annotations

import numpy as np

from chirp_geometry import square_time_scale, reference_normals


def sample_bilinear(times_ms, freqs_khz, db, query_t, query_f):
    """Bilinear sample, NaN outside grid, for 1D/2D query coordinates."""
    t = np.asarray(times_ms, dtype=float)
    f = np.asarray(freqs_khz, dtype=float)
    z = np.asarray(db, dtype=float)
    qt, qf = np.broadcast_arrays(np.asarray(query_t, dtype=float),
                                  np.asarray(query_f, dtype=float))
    if len(t) < 2 or len(f) < 2 or z.shape != (len(f), len(t)):
        raise ValueError("invalid time/frequency grid")
    if np.any(np.diff(t) <= 0) or np.any(np.diff(f) <= 0):
        raise ValueError("time and frequency grids must increase")
    flat_t, flat_f = qt.ravel(), qf.ravel()
    valid = np.isfinite(flat_t) & np.isfinite(flat_f)
    valid &= (flat_t >= t[0]) & (flat_t <= t[-1])
    valid &= (flat_f >= f[0]) & (flat_f <= f[-1])
    out = np.full(flat_t.shape, np.nan)
    if not np.any(valid):
        return out.reshape(qt.shape)
    a = np.clip(np.searchsorted(t, flat_t[valid], side="right") - 1, 0, len(t)-2)
    b = np.clip(np.searchsorted(f, flat_f[valid], side="right") - 1, 0, len(f)-2)
    wt = (flat_t[valid] - t[a]) / (t[a+1]-t[a])
    wf = (flat_f[valid] - f[b]) / (f[b+1]-f[b])
    values = ((1-wt)*(1-wf)*z[b,a] + wt*(1-wf)*z[b,a+1]
              + (1-wt)*wf*z[b+1,a] + wt*wf*z[b+1,a+1])
    out[valid] = values
    return out.reshape(qt.shape)


def continuous_normal_ridge(times_ms, freqs_khz, db,
                            annotation_t_ms, annotation_f_khz,
                            *, count=20, radius_khz=8.0, step_khz=0.5,
                            continuity_penalty=0.7,
                            anchor_penalty=0.1, max_jump_khz=3.0,
                            noise_db=None):
    """Viterbi ridge across perpendicular profiles using globally continuous path.

    At each of count equally spaced reference times, interpolate the manual
    polyline then sample along its normal in normalized (u,f) space.

    Score each candidate as local dB above the off-call noise floor, capped to
    avoid one loud intersecting harmonic dominating every decision. Penalize
    normal displacement, abrupt jumps and unsupported paths. Invalid candidates
    are forbidden. Positive offsets correspond to the normal pointing toward
    higher frequency (because its y component is always positive).
    """
    t = np.asarray(annotation_t_ms, dtype=float)
    f = np.asarray(annotation_f_khz, dtype=float)
    if len(t) < 2 or t.shape != f.shape or not np.all(np.isfinite(t)) or not np.all(np.isfinite(f)):
        raise ValueError("invalid annotation trajectory")
    if np.any(np.diff(t) <= 0):
        raise ValueError("annotation time must increase")
    if count < 3 or radius_khz <= 0 or step_khz <= 0 or max_jump_khz <= 0:
        raise ValueError("invalid ridge sampling parameters")
    if not np.isfinite([continuity_penalty,anchor_penalty]).all() or min(continuity_penalty,anchor_penalty) < 0:
        raise ValueError("penalties must be nonnegative")

    sample_t = np.linspace(t[0], t[-1], count)
    geometry = reference_normals(t, f, sample_t)
    sample_f = geometry["reference_freqs_khz"]
    normal_u = geometry["normal_u"]
    normal_f = geometry["normal_f"]
    scale = geometry["time_scale_khz_per_ms"]
    normalization_mode = geometry["normalization_mode"]
    offsets = np.arange(-radius_khz, radius_khz+step_khz*0.5, step_khz)
    candidate_t = sample_t[:, None] + (normal_u[:, None] * offsets) / scale
    candidate_f = sample_f[:, None] + normal_f[:, None] * offsets
    profiles_db = sample_bilinear(times_ms, freqs_khz, db, candidate_t, candidate_f)
    valid = np.isfinite(profiles_db)
    # Reject fully outside profiles before computing a nanpercentile baseline.
    # Otherwise NumPy emits RuntimeWarning: All-NaN slice encountered.
    if np.any(~np.any(valid, axis=1)):
        raise ValueError("normal profile outside spectrogram at one or more reference samples")
    # For local contrast we estimate per-frequency noise in the GUI blend;
    # do not use calibration detector's SNR thresholds here.
    if noise_db is not None:
        noise = np.asarray(noise_db, dtype=float)
        if noise.shape != np.asarray(freqs_khz).shape:
            raise ValueError("noise_db shape mismatch")
        relative_noise = np.interp(candidate_f, freqs_khz, noise, left=np.nan, right=np.nan)
        evidence = profiles_db - relative_noise
    else:
        evidence = profiles_db - np.nanpercentile(profiles_db, 20, axis=1)[:, None]
    evidence = np.clip(evidence, -15.0, 35.0)
    evidence[~valid] = -np.inf
    k = len(offsets)
    scores = np.full((count,k), -np.inf)
    back = np.zeros((count,k), dtype=np.int32)
    base = anchor_penalty*offsets**2
    scores[0] = evidence[0] - base
    jump = offsets[:,None] - offsets[None,:]    # current - previous
    transition = continuity_penalty * jump**2
    transition[np.abs(jump)>max_jump_khz+1e-9] = np.inf
    for index in range(1,count):
        total = scores[index-1][None,:] - transition
        best_prev = np.argmax(total,axis=1)
        back[index] = best_prev
        scores[index] = (evidence[index] - base
                         + total[np.arange(k),best_prev])
    if not np.isfinite(scores[-1]).any():
        raise ValueError("no valid continuous ridge within configured jump")
    states = np.empty(count,dtype=np.int32)
    states[-1] = int(np.argmax(scores[-1]))
    for index in range(count-1,0,-1):
        states[index-1] = back[index,states[index]]
    signed_normal = offsets[states]
    ridge_t = candidate_t[np.arange(count),states]
    ridge_f = candidate_f[np.arange(count),states]
    # Positive is above the reference in frequency, negative below.
    signed_frequency = ridge_f - sample_f
    support = np.abs(signed_normal)<=3.0
    return {
        "reference_times_ms": sample_t, "reference_freqs_khz": sample_f,
        "ridge_times_ms": ridge_t, "ridge_freqs_khz": ridge_f,
        "signed_normal_khz": signed_normal,  # legacy: geometric kHz_eq
        "signed_normal_khz_eq": signed_normal,
        "signed_frequency_khz": signed_frequency,
        "median_signed_normal_khz": float(np.median(signed_normal)),
        "median_abs_normal_khz": float(np.median(np.abs(signed_normal))),
        "p90_abs_normal_khz": float(np.percentile(np.abs(signed_normal),90)),
        "median_signed_normal_khz_eq": float(np.median(signed_normal)),
        "median_abs_normal_khz_eq": float(np.median(np.abs(signed_normal))),
        "p90_abs_normal_khz_eq": float(np.percentile(np.abs(signed_normal),90)),
        "median_signed_frequency_khz": float(np.median(signed_frequency)),
        "support_fraction_3khz": float(np.mean(support)),
        "support_fraction_3khz_eq": float(np.mean(support)),
        "samples_evaluated": count, "time_scale_khz_per_ms": scale,
        "normalization_mode": normalization_mode,
        "manual_time_span_ms": float(t[-1] - t[0]),
        "manual_frequency_span_khz": float(np.ptp(f)),
        "normal_radius_khz": radius_khz, "normal_step_khz": step_khz,
        "path_score": float(scores[-1].max()/count),
        "profiles_db": profiles_db,
        "normal_offsets_khz": offsets,  # legacy: geometric kHz_eq
        "normal_offsets_khz_eq": offsets,
        "normal_u": normal_u, "normal_f": normal_f,
    }
