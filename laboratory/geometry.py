"""Independent time-frequency chirp geometry benchmark (protocol v1 draft).

Coordinates are (time in ms, frequency in kHz). Calibration is based *only*
on the manual curve: the main slope becomes -45 degrees through axis scaling.
The score samples local normals at equal arclength and matches them to an
automatic trajectory with a globally ordered, maximum-quality sequence.

The default thresholds are proposals from SPEC_Benchmark_Chirps_Geometrie_v0.1;
real-data calibration has not yet been performed.
"""

from __future__ import annotations

from dataclasses import asdict, dataclass
from math import sqrt
from typing import Any

import numpy as np
from scipy.interpolate import PchipInterpolator
from scipy.optimize import linear_sum_assignment
from scipy.stats import theilslopes


@dataclass(frozen=True)
class GeometryConfig:
    n_normals: int = 20
    dense_points: int = 256
    min_slope_khz_per_ms: float = 1.0
    fallback_scale_khz_per_ms: float = 10.0
    search_radius_factor: float = 2.0
    min_match_coverage: float = 0.40
    max_match_median_normalized_error: float = 1.0
    min_match_score: float = 0.0
    tangent_tail_fraction: float = 0.20
    tail_angle_deg: float = 20.0
    tail_lateral_factor: float = 1.0
    tail_extreme_factor: float = 2.0
    check_start_extension: bool = False

    def __post_init__(self):
        if self.n_normals < 3 or self.dense_points < max(24, self.n_normals * 2):
            raise ValueError("Insufficient geometric sampling")
        for attr in ("min_slope_khz_per_ms", "fallback_scale_khz_per_ms",
                     "search_radius_factor", "max_match_median_normalized_error",
                     "tangent_tail_fraction", "tail_lateral_factor",
                     "tail_extreme_factor"):
            if getattr(self, attr) <= 0:
                raise ValueError(f"{attr} must be positive")
        if not 0 < self.min_match_coverage <= 1 or not 0 < self.tangent_tail_fraction <= 1:
            raise ValueError("Invalid coverage or tail fraction")
        if not 0 < self.tail_angle_deg <= 180:
            raise ValueError("tail_angle_deg must be in (0, 180]")


def tolerance_khz(snr_db: float) -> float:
    return float(np.interp(float(snr_db), [15.0, 35.0], [5.0, 3.0]))


def importance_weight(snr_db: float) -> float:
    return 1.0 + 2.0 * float(np.clip((float(snr_db) - 15.0) / 20.0, 0, 1))


def _curve(points: Any) -> np.ndarray:
    """Validate and sort a curve. Duplicated times are errors, never hidden."""
    if isinstance(points, np.ndarray):
        arr = np.asarray(points, dtype=float)
    elif points and isinstance(points[0], dict):
        arr = np.asarray([[p["t_ms"], p["f_khz"]] for p in points], dtype=float)
    else:
        arr = np.asarray(points, dtype=float)
    if arr.ndim != 2 or arr.shape[1] != 2 or len(arr) < 2:
        raise ValueError("Chirp curve requires at least two (t_ms, f_khz) points")
    if not np.isfinite(arr).all():
        raise ValueError("Chirp curve contains non-finite coordinates")
    arr = arr[np.argsort(arr[:, 0], kind="stable")]
    if np.any(np.diff(arr[:, 0]) <= 0):
        raise ValueError("Chirp curve contains duplicate/non-increasing times")
    return arr


def _dense(curve: np.ndarray, count: int) -> np.ndarray:
    times = np.linspace(curve[0, 0], curve[-1, 0], count)
    if len(curve) == 2:
        freqs = np.interp(times, curve[:, 0], curve[:, 1])
    else:
        freqs = PchipInterpolator(curve[:, 0], curve[:, 1])(times)
    return np.column_stack((times, freqs))


def _arclength(xy: np.ndarray) -> np.ndarray:
    return np.r_[0.0, np.cumsum(np.linalg.norm(np.diff(xy, axis=0), axis=1))]


def _unit(v: np.ndarray) -> np.ndarray:
    norm = np.linalg.norm(v, axis=-1, keepdims=True)
    return v / np.maximum(norm, 1e-12)


def _main_slope(manual: np.ndarray, config: GeometryConfig) -> tuple[float, bool]:
    # Densifying and interpolating by the original (t,f) arclength removes
    # the unequal point spacing introduced by editing annotations manually.
    dense = _dense(manual, config.dense_points)
    arc = _arclength(dense)
    at = np.linspace(arc[0], arc[-1], min(100, config.dense_points))
    t = np.interp(at, arc, dense[:, 0])
    f = np.interp(at, arc, dense[:, 1])
    slope = float(theilslopes(f, t)[0])
    degraded = not np.isfinite(slope) or abs(slope) < config.min_slope_khz_per_ms
    if degraded:
        sign = -1 if not np.isfinite(slope) or slope <= 0 else 1
        slope = sign * config.fallback_scale_khz_per_ms
    return slope, degraded


def _normalised(points: np.ndarray, t0: float, f0: float,
                slope: float) -> np.ndarray:
    return np.column_stack((abs(slope) * (points[:, 0] - t0),
                            -np.sign(slope) * (points[:, 1] - f0)))


def _sample_normals(manual_xy: np.ndarray, n: int):
    arc = _arclength(manual_xy)
    sampled_at = np.linspace(arc[0], arc[-1], n)
    samples = np.column_stack((np.interp(sampled_at, arc, manual_xy[:, 0]),
                               np.interp(sampled_at, arc, manual_xy[:, 1])))
    # Interpolate dense local derivatives; taking the gradient of only the 20
    # sample points would smooth away sharp knees.
    dense_tangent = _unit(np.gradient(manual_xy, arc, axis=0))
    tangents = _unit(np.column_stack((
        np.interp(sampled_at, arc, dense_tangent[:, 0]),
        np.interp(sampled_at, arc, dense_tangent[:, 1]))))
    normals = np.column_stack((-tangents[:, 1], tangents[:, 0]))
    return sampled_at, samples, normals


def _intersections(point: np.ndarray, normal: np.ndarray, auto_xy: np.ndarray,
                   max_dist: float):
    segment = auto_xy[1:] - auto_xy[:-1]
    offset = auto_xy[:-1] - point
    cross_segment = segment[:, 0] * normal[1] - segment[:, 1] * normal[0]
    cross_offset = offset[:, 0] * normal[1] - offset[:, 1] * normal[0]
    valid = abs(cross_segment) > 1e-12
    u = np.full(len(segment), np.nan)
    u[valid] = -cross_offset[valid] / cross_segment[valid]
    valid &= (u >= -1e-9) & (u <= 1 + 1e-9)
    idx = np.flatnonzero(valid)
    if not len(idx):
        return []
    u = np.clip(u[idx], 0, 1)
    intersections = auto_xy[idx] + u[:, None] * segment[idx]
    distances = np.einsum("ij,j->i", intersections - point, normal)
    result = [(float(j + frac), float(d), intersections[k].tolist())
              for k, (j, frac, d) in enumerate(zip(idx, u, distances))
              if abs(d) <= max_dist]
    # A normal crossing an automatic vertex can appear twice. Deduplicate it.
    result.sort(key=lambda r: (r[0], abs(r[1])))
    unique = []
    for candidate in result:
        if not unique or abs(candidate[0] - unique[-1][0]) > 1e-7:
            unique.append(candidate)
    return unique


def _ordered_matches(samples, normals, auto_xy, tol_perp, config):
    """Maximise total quality over a strictly increasing automatic path."""
    states = []  # (manual_index, auto_progress, accumulated_quality, predecessor, candidate)
    for i, (point, normal) in enumerate(zip(samples, normals)):
        for progress, distance, hit in _intersections(
                point, normal, auto_xy, config.search_radius_factor * tol_perp):
            q = float(np.exp(- (distance / tol_perp) ** 2))
            best_quality, best_previous = q, None
            for state_idx, (prev_i, prev_progress, quality, _, _) in enumerate(states):
                if prev_i < i and prev_progress + 1e-7 < progress and quality + q > best_quality:
                    best_quality, best_previous = quality + q, state_idx
            states.append((i, progress, best_quality, best_previous,
                           (distance, q, hit)))
    selected = {}
    if states:
        idx = max(range(len(states)), key=lambda j: states[j][2])
        while idx is not None:
            i, progress, quality, previous, candidate = states[idx]
            selected[i] = (progress, *candidate)
            idx = previous
    return selected


def _tail_penalty(manual_xy, auto_curve, auto_xy, t_limit, tolerance, config,
                  *, at_start=False):
    """Evaluate extra automatic trajectory outside a manual endpoint."""
    beyond = auto_curve[:, 0] < t_limit if at_start else auto_curve[:, 0] > t_limit
    if not np.any(beyond):
        return {"length": 0.0, "invalid_length": 0.0, "penalty": 0.0}

    manual_arc = _arclength(manual_xy)
    length = manual_arc[-1]
    fraction = config.tangent_tail_fraction * length
    if at_start:
        endpoint = manual_xy[0]
        base = np.column_stack((np.interp([fraction], manual_arc, manual_xy[:, 0]),
                                np.interp([fraction], manual_arc, manual_xy[:, 1])))[0]
        direction = _unit(endpoint - base)
        selected = np.flatnonzero(beyond)
        boundary = [np.interp(t_limit, auto_curve[:, 0], auto_xy[:, k])
                    for k in (0, 1)]
        tail = np.vstack((auto_xy[selected], boundary))
    else:
        endpoint = manual_xy[-1]
        base = np.array([np.interp(length - fraction, manual_arc, manual_xy[:, k])
                         for k in (0, 1)])
        direction = _unit(endpoint - base)
        selected = np.flatnonzero(beyond)
        boundary = np.array([np.interp(t_limit, auto_curve[:, 0], auto_xy[:, k])
                             for k in (0, 1)])
        tail = np.vstack((boundary, auto_xy[selected]))

    segments = np.diff(tail, axis=0)
    seg_len = np.linalg.norm(segments, axis=1)
    valid_len = seg_len > 1e-12
    midpoints = tail[:-1] + 0.5 * segments
    displacement = midpoints - endpoint
    forward = displacement @ direction
    lateral = abs(displacement[:, 0] * direction[1] -
                  displacement[:, 1] * direction[0])
    alignment = np.einsum("ij,j->i", _unit(segments), direction)
    # For the initial extension the automatic path runs towards the manual
    # endpoint, so reverse its local orientation for comparison.
    if at_start:
        alignment = -alignment
    wrong = ((forward < -1e-8) |
             (lateral > config.tail_lateral_factor * tolerance) |
             (lateral > config.tail_extreme_factor * tolerance) |
             (alignment < np.cos(np.deg2rad(config.tail_angle_deg)))) & valid_len
    total = float(np.sum(seg_len))
    invalid = float(np.sum(seg_len[wrong]))
    return {"length": total, "invalid_length": invalid,
            "penalty": invalid / (length + total) if length + total else 0.0}


def score_pair(manual_points, automatic_points, snr_max_db: float,
               config: GeometryConfig = GeometryConfig()) -> dict:
    """Score one independent manual/automatic pair, from 0 to 100."""
    manual = _curve(manual_points)
    if not np.isfinite(snr_max_db):
        raise ValueError("Manual peak SNR must be finite")
    slope, degraded = _main_slope(manual, config)
    t0, f0 = manual[0]
    manual_dense = _dense(manual, config.dense_points)
    manual_xy = _normalised(manual_dense, t0, f0, slope)
    length = float(_arclength(manual_xy)[-1])
    if length <= 1e-12:
        raise ValueError("Manual curve has no geometric length")
    position, samples, normals = _sample_normals(manual_xy, config.n_normals)
    tol_freq = tolerance_khz(snr_max_db)
    tol_perp = tol_freq / sqrt(2)
    selected = {}
    after = {"length": 0.0, "invalid_length": 0.0, "penalty": 0.0}
    before = after.copy()
    if automatic_points is not None:
        automatic = _curve(automatic_points)
        auto_dense = _dense(automatic, config.dense_points)
        auto_xy = _normalised(auto_dense, t0, f0, slope)
        selected = _ordered_matches(samples, normals, auto_xy, tol_perp, config)
        if automatic[-1, 0] > manual[-1, 0]:
            after = _tail_penalty(manual_xy, auto_dense, auto_xy,
                                  manual[-1, 0], tol_perp, config)
        if config.check_start_extension and automatic[0, 0] < manual[0, 0]:
            before = _tail_penalty(manual_xy, auto_dense, auto_xy,
                                   manual[0, 0], tol_perp, config, at_start=True)
    points = []
    for i, (manual_xy_point, normal) in enumerate(zip(samples, normals)):
        match = selected.get(i)
        distance, q = (match[1], match[2]) if match else (None, 0.0)
        points.append({
            "fraction": float(position[i] / length),
            "manual_time_ms": float(t0 + manual_xy_point[0] / abs(slope)),
            "manual_frequency_khz": float(f0 - np.sign(slope) * manual_xy_point[1]),
            "normal": normal.tolist(),
            "distance_signed_equivalent_khz": distance,
            "error_equivalent_khz": abs(distance) * sqrt(2) if match else None,
            "automatic_point": (
                {"time_ms": float(t0 + match[3][0] / abs(slope)),
                 "frequency_khz": float(f0 - np.sign(slope) * match[3][1])}
                if match else None),
            "covered": bool(match), "quality": q,
        })
    covered = [p for p in points if p["covered"]]
    coverage = len(covered) / config.n_normals
    quality = float(np.mean([p["quality"] for p in covered])) if covered else 0.0
    ext_penalty = 1 - (1 - after["penalty"]) * (1 - before["penalty"])
    score = float(100 * coverage * quality * (1 - ext_penalty))
    errors = [abs(p["distance_signed_equivalent_khz"]) / tol_perp for p in covered]
    median_normalized = float(np.median(errors)) if errors else None
    return {
        "score": score, "coverage": coverage, "quality": quality,
        "extension_penalty": ext_penalty, "end_extension": after,
        "start_extension": before,
        "median_normalized_error": median_normalized,
        "p90_normalized_error": float(np.percentile(errors, 90)) if errors else None,
        "snr_max_db": float(snr_max_db), "snr_weight": importance_weight(snr_max_db),
        "tolerance_frequency_khz": tol_freq,
        "tolerance_perpendicular_khz": tol_perp,
        "main_slope_khz_per_ms": slope,
        "normalization_degraded": degraded,
        "n_normal_samples": config.n_normals,
        "samples": points,
    }


def recognised(result: dict, config: GeometryConfig = GeometryConfig()) -> bool:
    median = result["median_normalized_error"]
    return (result["coverage"] >= config.min_match_coverage and
            median is not None and
            median <= config.max_match_median_normalized_error and
            result["score"] > config.min_match_score)


def match_curves(manual: list[dict], automatic: list[dict],
                 config: GeometryConfig = GeometryConfig(),
                 *, exhaustive: bool = False) -> dict:
    """Globally assign autos to manuals, keeping harmonics as separate calls.

    Manual: {id, points, snr_max_db}; auto: {id, points}. Each automatic curve
    is assigned at most once, even when two manual chirps overlap in time.
    """
    candidates = {}
    costs = np.zeros((len(manual), len(automatic) + len(manual)))
    for i, ref in enumerate(manual):
        for j, predicted in enumerate(automatic):
            # Basic time-envelope rejection avoids scoring impossible pairs.
            m = _curve(ref["points"])
            a = _curve(predicted["points"])
            if a[-1, 0] < m[0, 0] or m[-1, 0] < a[0, 0]:
                continue
            result = score_pair(ref["points"], predicted["points"],
                                ref["snr_max_db"], config)
            candidates[i, j] = result
            if recognised(result, config):
                costs[i, j] = -result["snr_weight"] * result["score"]
    assignments = {}
    if len(manual):
        rows, cols = linear_sum_assignment(costs)
        for i, j in zip(rows, cols):
            if j < len(automatic) and costs[i, j] < 0:
                assignments[int(i)] = int(j)
    details = []
    for i, ref in enumerate(manual):
        j = assignments.get(i)
        result = candidates.get((i, j)) if j is not None else score_pair(
            ref["points"], None, ref["snr_max_db"], config)
        details.append({"manual_id": ref["id"],
                        "automatic_id": automatic[j]["id"] if j is not None else None,
                        "detected": j is not None, **result})
    assigned = set(assignments.values())
    unmatched_auto = [{"automatic_id": a["id"],
                       "status": "false_positive" if exhaustive else "unassessed"}
                      for j, a in enumerate(automatic) if j not in assigned]
    weight_total = sum(d["snr_weight"] for d in details)
    return {
        "details": details, "unmatched_automatic": unmatched_auto,
        "weighted_recall": (sum(d["snr_weight"] for d in details if d["detected"])
                            / weight_total if weight_total else None),
        "weighted_trajectory_score": (
            sum(d["snr_weight"] * d["score"] for d in details)
            / (100 * weight_total) if weight_total else None),
        "false_positive_count": len(unmatched_auto) if exhaustive else None,
        "config": asdict(config),
    }
