"""Shared, zoom-independent square time/frequency geometry for bat chirps.

Times are milliseconds, frequencies are kHz, distances are kHz equivalents.
The manual annotation alone determines the time scale; predictions never do.
"""
from __future__ import annotations

import numpy as np

PROTOCOL = "square_normals_v1"
TIME_SCALE_POLICY = "cubic_near_horizontal_transition_v2"


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
    if scale == 0:
        return float(fallback_khz_per_ms), "near_horizontal_fallback"
    if scale < near_horizontal_limit_khz_per_ms:
        # Blend the near-horizontal fallback continuously into the square
        # calibration. The cubic smoothstep has zero derivative at both ends,
        # so the effective scale joins the raw scale continuously (including
        # its first derivative) at the threshold. This also avoids a nearly
        # tenfold jump when an annotation crosses 1 kHz/ms.
        fraction = scale / near_horizontal_limit_khz_per_ms
        blend = fraction * fraction * (3.0 - 2.0 * fraction)
        continuous_scale = (fallback_khz_per_ms * (1.0 - blend)
                            + scale * blend)
        return float(continuous_scale), "near_horizontal_transition"
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

    # Each state stores: predecessor state, reference sample, intersection,
    # number of matched normals, accumulated absolute distance, last
    # prediction-polyline progress. State 0 represents an empty path.
    #
    # Keeping a state per possible last intersection allows earlier matches
    # to be reconsidered when a locally closest one would block a later
    # normal. We maximize coverage first, then minimize the total error.
    # Skipping a reference normal retains all existing states unchanged.
    states = [(None, None, None, 0, 0.0, -np.inf)]
    frontier = [0]

    def rank(state_index):
        state = states[state_index]
        return state[3], -state[4], -state[5]

    for i, (ts, fs, nu, nf) in enumerate(zip(x, ref["reference_freqs_khz"],
                                           ref["normal_u"], ref["normal_f"])):
        mu = (ts - t[0]) * scale
        # Tangent = (nf,-nu), perpendicular to the normal (nu,nf).
        a = (p0u - mu) * nf - (p0f - fs) * nu
        delta = du * nf - df * nu
        with np.errstate(divide="ignore", invalid="ignore"):
            alpha = -a / delta
        coincident = (np.abs(delta) < 1e-10) & (np.abs(a) < 1e-10)
        denom = du * du + df * df
        closest = np.clip(((mu - p0u) * du + (fs - p0f) * df)
                          / np.maximum(denom, 1e-20), 0, 1)

        intersections = []
        for segment in range(len(du)):
            # If the entire segment lies on the normal, keep its endpoints
            # as well as the closest point: a later normal may require an
            # earlier or later point on this same coincident segment.
            fractions = ((0.0, float(closest[segment]), 1.0)
                         if coincident[segment]
                         else (float(alpha[segment]),))
            for fraction in fractions:
                if not np.isfinite(fraction) or not -1e-9 <= fraction <= 1 + 1e-9:
                    continue
                fraction = float(np.clip(fraction, 0, 1))
                distance = ((p0u[segment] + fraction * du[segment] - mu) * nu
                            + (p0f[segment] + fraction * df[segment] - fs) * nf)
                if (max_radius_khz_eq is not None
                        and abs(distance) > max_radius_khz_eq):
                    continue
                progress = float(segment + fraction)
                time = float(pt[segment] + fraction * (pt[segment+1]-pt[segment]))
                frequency = float(p0f[segment] + fraction * df[segment])
                intersections.append((progress, float(distance), time, frequency))

        new_states = []
        for intersection in intersections:
            progress, distance, _, _ = intersection
            eligible = (node for node in frontier
                        if states[node][5] <= progress + 1e-9)
            predecessor = max(eligible, key=rank, default=None)
            if predecessor is None:
                continue
            previous = states[predecessor]
            states.append((predecessor, i, intersection, previous[3] + 1,
                           previous[4] + abs(distance), progress))
            new_states.append(len(states) - 1)
        frontier.extend(new_states)

    best = max(frontier, key=rank)
    while best != 0:
        predecessor, index, intersection, _, _, _ = states[best]
        _, distance, time, frequency = intersection
        signed[index] = distance
        hit_t[index] = time
        hit_f[index] = frequency
        best = predecessor
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


def point_distance_to_manual_curve(annotation_t_ms, annotation_f_khz,
                                   point_t_ms, point_f_khz):
    """Shortest point-to-manual-polyline distance in geometric kHz_eq.

    Used for initial detector-peak matching only; trajectory evaluation
    still uses ordered intersections of manual normals, not nearest points.
    """
    t = np.asarray(annotation_t_ms, dtype=float)
    f = np.asarray(annotation_f_khz, dtype=float)
    scale, _ = square_time_scale(t, f)
    if not np.isfinite([point_t_ms, point_f_khz]).all():
        raise ValueError("invalid candidate peak")
    u = (t - t[0]) * scale
    point_u = (float(point_t_ms) - t[0]) * scale
    point_f = float(point_f_khz)
    du, df = np.diff(u), np.diff(f)
    proj = ((point_u-u[:-1])*du + (point_f-f[:-1])*df)
    alpha = np.clip(proj / np.maximum(du*du+df*df, 1e-20), 0., 1.)
    return float(np.min(np.hypot(point_u-u[:-1]-alpha*du,
                                 point_f-f[:-1]-alpha*df)))
