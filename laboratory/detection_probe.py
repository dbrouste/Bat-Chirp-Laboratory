"""Read-only Python return-event observation of the actual V4 benchmark."""

from __future__ import annotations

import ast
from collections import Counter
from dataclasses import fields, is_dataclass
import hashlib
import inspect
import json
from pathlib import Path
import sys

import numpy as np
import soundfile as sf


def canonical(value):
    if isinstance(value, np.ndarray):
        return {"array_dtype": str(value.dtype), "shape": list(value.shape),
                "sha256": hashlib.sha256(np.ascontiguousarray(value).tobytes()).hexdigest()}
    if isinstance(value, np.generic):
        return canonical(value.item())
    if isinstance(value, float) and not np.isfinite(value):
        return "NaN" if np.isnan(value) else "Infinity" if value > 0 else "-Infinity"
    if isinstance(value, slice):
        return {"slice": [value.start, value.stop, value.step]}
    if is_dataclass(value):
        return {field.name: canonical(getattr(value, field.name)) for field in fields(value)}
    if isinstance(value, dict):
        return {str(key): canonical(item) for key, item in sorted(value.items()) if key != "timings_ms"}
    if isinstance(value, (list, tuple)):
        return [canonical(item) for item in value]
    return value


def digest(value):
    return hashlib.sha256(json.dumps(canonical(value), sort_keys=True, allow_nan=False).encode()).hexdigest()


def group_outcome(tracking, audio, rate, candidates, tracking_kwargs):
    try:
        return tracking.track_and_group_candidates(audio, rate, candidates, tracking_kwargs=tracking_kwargs), None
    except MemoryError:
        raise
    except Exception as exc:
        return None, {"type": type(exc).__name__, "message": str(exc)}


def compact(candidate):
    return {key: canonical(value) for key, value in candidate.items()
            if isinstance(value, (str, int, float, bool, np.generic)) or value is None}


def return_guards(function):
    source = Path(inspect.getsourcefile(function)).read_text(encoding="utf-8")
    tree = ast.parse(source)
    result = {}

    def visit(node, conditions):
        if isinstance(node, ast.Return) and isinstance(node.value, ast.Constant) and node.value.value is None:
            result[node.lineno] = list(conditions)
        for child in ast.iter_child_nodes(node):
            visit(child, conditions + [ast.unparse(node.test)] if isinstance(node, ast.If) and child in node.body else conditions)

    target = next(node for node in tree.body if isinstance(node, ast.FunctionDef) and node.name == function.__name__)
    visit(target, [])
    return result


class ReturnObserver:
    def __init__(self, detector, tracking, benchmark):
        self.detector, self.tracking, self.benchmark = detector, tracking, benchmark
        self.functions = {function.__code__: name for name, function in (
            ("extract", detector._extract_candidates), ("detect", detector.detect_candidates_snr_blobs),
            ("nms", detector._time_freq_nms), ("track", tracking.track_reassigned_candidate),
            ("ridge", tracking.track_reassigned_ridge), ("match", benchmark.match_candidates))}
        self.raw = []
        self.mask_ids = {}
        self.stages = {}
        self.tracks = []
        self.ridge_events = []
        self.matches = []
        self.manual_ids = {}
        self.parameters = {}
        self.final_hash = None
        self.guards = return_guards(tracking.track_reassigned_ridge)
        self.source = {name: {"file": str(Path(inspect.getsourcefile(module))),
                              "sha256": hashlib.sha256(Path(inspect.getsourcefile(module)).read_bytes()).hexdigest()}
                       for name, module in (("detector", detector), ("tracking", tracking), ("benchmark", benchmark))}

    def uid(self, candidate):
        return self.mask_ids.get(id(candidate.get("binary_mask_slice")), "unresolved")

    def trace(self, frame, event, argument):
        if event == "call" and frame.f_code in self.functions:
            frame.f_trace_lines = False
            return self.local_trace
        return None

    def local_trace(self, frame, event, argument):
        if event != "return":
            return self.local_trace
        name = self.functions[frame.f_code]
        values = frame.f_locals
        if name == "extract":
            extraction = len(self.raw)
            candidates = []
            for index, candidate in enumerate(argument):
                uid = f"raw:{extraction}:{index}"
                self.mask_ids[id(candidate["binary_mask_slice"])] = uid
                candidates.append({"uid": uid, **compact(candidate)})
            self.raw.append({"parameters": {key: canonical(values[key]) for key in
                ("snr_threshold_db", "percentile_q", "fmin", "fmax", "n_fft", "hop")}, "candidates": candidates})
        elif name == "detect":
            if values.get("slope_filter_mode") != "adaptive_v4":
                raise RuntimeError("This observer supports the audited adaptive_v4 branch only")
            for stage in ("general_raw", "general", "general_v4", "low_raw", "low_v4"):
                if stage not in values:
                    raise RuntimeError(f"Audited detector stage is unavailable: {stage}")
                self.stages[stage] = [{"uid": self.uid(candidate), **compact(candidate)} for candidate in values[stage]]
            self.stages["final"] = [{"uid": self.uid(candidate), **compact(candidate)} for candidate in argument]
            self.final_hash = digest(argument)
            self.parameters.update({key: canonical(values[key]) for key in inspect.signature(self.detector.detect_candidates_snr_blobs).parameters
                                    if key not in {"y", "sr"}})
        elif name == "nms":
            self.stages["nms_input"] = [{"uid": self.uid(candidate), **compact(candidate)} for candidate in values["candidates"]]
            self.parameters["observed_nms_window_ms"] = values["window_ms"]
        elif name == "ridge":
            self.ridge_events.append({"returned_curve": argument is not None,
                "source_line": frame.f_lineno, "source_guards": self.guards.get(frame.f_lineno, []),
                "diagnostics": canonical(values.get("diagnostics")),
                "anchor_time_s": values.get("time_mid"), "anchor_freq_hz": values.get("seed_freq_hz")})
        elif name == "track":
            curve = argument
            row = {"uid": self.uid(values["candidate"]), "curve_hash": digest(curve), "accepted": curve is not None,
                   "seed_time_s": values.get("seed_time"), "seed_freq_hz": values.get("seed_freq"),
                   "time_bounds_s": values.get("bounds"), "ridge": self.ridge_events[-1] if self.ridge_events else None}
            if curve is not None:
                row.update(start_s=float(curve[0, 0]), end_s=float(curve[-1, 0]),
                           start_hz=float(curve[0, 1]), end_hz=float(curve[-1, 1]), points=len(curve))
            self.tracks.append(row)
        elif name == "match":
            self.matches = list(argument)
            self.manual_ids = {index: annotation[0] for index, annotation in enumerate(values["annotations"])}
        return self.local_trace

    def __enter__(self):
        if sys.gettrace() is not None:
            raise RuntimeError("Refusing to replace an existing debugger/tracer")
        sys.settrace(self.trace)
        return self

    def __exit__(self, *args):
        sys.settrace(None)


def related(candidates, annotation, matcher, geometry_mode):
    return [candidate for candidate in candidates
            if matcher([candidate], [annotation], geometry_mode=geometry_mode)]


def rejected_conditions(candidate, stage, parameters):
    if not parameters:
        return []
    number = lambda key: float(candidate[key])
    checks = []
    if stage == "general_filters":
        checks = [("size", number("size"), ">", parameters["min_blob_size"], parameters["min_blob_size"] <= 0 or number("size") > parameters["min_blob_size"]),
                  ("height_hz", number("height_hz"), ">", parameters["min_blob_height_hz"], parameters["min_blob_height_hz"] <= 0 or number("height_hz") > parameters["min_blob_height_hz"]),
                  ("slope_hz_per_ms", number("slope_hz_per_ms"), "<=", parameters["general_max_blob_slope_hz_per_ms"], number("slope_hz_per_ms") <= parameters["general_max_blob_slope_hz_per_ms"])]
    elif stage == "general_v4_width":
        checks = [("width_ms", number("width_ms"), ">=", parameters["v4_general_min_width_ms"], number("width_ms") >= parameters["v4_general_min_width_ms"])]
    elif stage == "low_v4_filters":
        checks = [("f_high", number("f_high"), "<=", parameters["lowfreq_max_hz"], number("f_high") <= parameters["lowfreq_max_hz"]),
                  ("size", number("size"), ">", parameters["min_blob_size"], parameters["min_blob_size"] <= 0 or number("size") > parameters["min_blob_size"]),
                  ("height_hz", number("height_hz"), ">=", parameters["lowfreq_min_blob_height_hz"], number("height_hz") >= parameters["lowfreq_min_blob_height_hz"]),
                  ("width_ms", number("width_ms"), ">=", parameters["v4_lowfreq_min_width_ms"], number("width_ms") >= parameters["v4_lowfreq_min_width_ms"]),
                  ("slope_hz_per_ms_lower", number("slope_hz_per_ms"), ">=", parameters["v4_lowfreq_min_slope_hz_per_ms"], number("slope_hz_per_ms") >= parameters["v4_lowfreq_min_slope_hz_per_ms"]),
                  ("slope_hz_per_ms_upper", number("slope_hz_per_ms"), "<", 0.0, number("slope_hz_per_ms") < 0)]
    return [{"field": name, "observed": canonical(value), "required_operator": operator, "threshold": threshold}
            for name, value, operator, threshold, passed in checks if not passed]


def diagnose_missing(annotation, observer, matcher, geometry_mode):
    final = related(observer.stages["final"], annotation, matcher, geometry_mode)
    pre_nms = related(observer.stages["nms_input"], annotation, matcher, geometry_mode)
    general = related(observer.stages["general"], annotation, matcher, geometry_mode)
    raw = related(observer.stages["general_raw"] + observer.stages["low_raw"], annotation, matcher, geometry_mode)
    _, times, frequencies, _ = annotation
    overlap = [candidate for candidate in observer.stages["general_raw"] + observer.stages["low_raw"]
               if candidate["t_start"] <= times[-1] and candidate["t_end"] >= times[0]
               and candidate["f_low"] <= max(frequencies) and candidate["f_high"] >= min(frequencies)]
    if final:
        category, stage, reason = "matching_failure", "global_assignment", "admissible_final_candidate_not_assigned"
    elif pre_nms:
        category, stage, reason = "known_rejection", "time_frequency_nms", "admissible_candidates_removed_by_observed_nms"
    elif general:
        category, stage, reason = "known_rejection", "general_v4_width", "admissible_general_candidates_do_not_survive_width_gate"
    elif raw:
        category, stage, reason = "known_rejection", "branch_filters", "admissible_raw_candidates_do_not_survive_branch_filters"
    elif not overlap:
        category, stage, reason = "no_candidate_observed", "spectral_blobs", "no_raw_blob_bbox_overlaps_manual_bbox_at_detector_thresholds"
    else:
        category, stage, reason = "indeterminate", "spectral_blobs", "raw_blob_bbox_overlap_but_no_benchmark_admissible_peak"
    memberships = {name: {row["uid"] for row in values} for name, values in observer.stages.items()}
    rejections = {}
    conditions = {}
    for candidate in raw:
        uid = candidate["uid"]
        if uid in memberships["general_raw"]:
            rejection = "general_filters" if uid not in memberships["general"] else "general_v4_width"
        else:
            rejection = "low_v4_filters"
        if uid in memberships["nms_input"] and uid not in memberships["final"]:
            rejection = "time_frequency_nms"
        if uid not in memberships["final"]:
            rejections[uid] = rejection
            conditions[uid] = rejected_conditions(candidate, rejection, getattr(observer, "parameters", {}))
    assignment = {candidate_index: getattr(observer, "manual_ids", {}).get(manual_index)
                  for candidate_index, manual_index in getattr(observer, "matches", [])}
    assignment_witnesses = [{"candidate_uid": row["uid"], "candidate_index": index,
                             "assigned_manual_chirp_id": assignment.get(index)}
                            for index, row in enumerate(observer.stages["final"]) if row in final]
    return {"category": category, "last_stage": stage, "reason": reason,
            "tracking_invoked_for_reference": False,
            "raw_bbox_overlap_count": len(overlap), "raw_admissible_count": len(raw),
            "evidence": {"raw": raw, "general": general, "pre_nms": pre_nms, "final": final},
            "observed_rejections_by_uid": rejections,
            "failed_filter_conditions_by_uid": conditions, "global_assignment_witnesses": assignment_witnesses,
            "detector_parameters": getattr(observer, "parameters", {}),
            "interpretation": "Candidate admissibility is the existing matcher gate, not proof of biological identity"}


def evaluate_with_probe(evaluate, annotation_path, wav_root, options):
    from bat_analysis import detection, reassignment_tracking as tracking
    from benchmark import reassignment_detector_seeds as benchmark

    native = evaluate(annotation_path, wav_root, **options)
    with ReturnObserver(detection, tracking, benchmark) as observer:
        observed = evaluate(annotation_path, wav_root, **options)
    if canonical(native) != canonical(observed):
        raise RuntimeError("Instrumentation changed benchmark decisions or numeric results")
    document = json.loads(Path(annotation_path).read_text(encoding="utf-8"))
    if len(document["files"]) != 1:
        raise ValueError("Diagnostic worker must process exactly one selected WAV")
    name, record = next(iter(document["files"].items()))
    audio, rate = sf.read(str(Path(wav_root) / name), always_2d=False)
    if audio.ndim == 2:
        audio = np.mean(audio, axis=1)
    detector_kwargs = {"slope_filter_mode": "adaptive_v4"}
    if options.get("v4_general_min_width_ms") is not None:
        detector_kwargs["v4_general_min_width_ms"] = options["v4_general_min_width_ms"]
    candidates = detection.detect_candidates_snr_blobs(np.asarray(audio), int(rate), **detector_kwargs)
    if digest(candidates) != observer.final_hash:
        raise RuntimeError("Instrumentation changed the full detector candidate outputs")
    annotations = benchmark._manual_annotations(record)
    details = {str(row["chirp_id"]): row for row in observed["details"]}
    tracking_kwargs = {key: options[key] for key in ("n_fft", "hop", "search_margin_ms", "trim_echo_rebound")
                       if options.get(key) is not None}
    tracking_kwargs["complete_ends"] = True
    failures, endpoints = [], []
    observed_tracks = {row["uid"]: row for row in observer.tracks}
    for annotation in annotations:
        chirp_id, times, frequencies, _ = annotation
        detail = details[str(chirp_id)]
        if not detail["detector_matched"]:
            failures.append({"file": name, "chirp_id": chirp_id, "snr_max_db": detail["snr_max_db"],
                             **diagnose_missing(annotation, observer, benchmark.match_candidates, options["geometry_mode"])})
            continue
        index = detail["candidate_index"]
        uid = observer.stages["final"][index]["uid"]
        trace = observed_tracks[uid]
        tracking_error = None
        try:
            original_curve = tracking.track_reassigned_candidate(audio, int(rate), candidates[index], diagnostics={}, **tracking_kwargs)
        except Exception as exc:
            original_curve = None
            tracking_error = f"{type(exc).__name__}: {exc}"
        if tracking_error != detail.get("diagnostics", {}).get("error"):
            raise RuntimeError("Instrumentation changed the matched tracking exception")
        if digest(original_curve) != trace["curve_hash"]:
            raise RuntimeError("Instrumentation changed a matched trajectory array")
        row = {"file": name, "chirp_id": chirp_id, "snr_max_db": detail["snr_max_db"],
               "duration_ms": float(1000 * (times[-1] - times[0])), "f_min_khz": float(min(frequencies) / 1000),
               "f_max_khz": float(max(frequencies) / 1000), "tracking_success": trace["accepted"],
               "start_signed_ms": None, "end_signed_ms": None, "tracking_error": tracking_error, "trace": trace}
        if trace["accepted"]:
            row.update(start_signed_ms=1000 * (trace["start_s"] - float(times[0])),
                       end_signed_ms=1000 * (trace["end_s"] - float(times[-1])),
                       start_abs_ms=detail["start_error_ms"], end_abs_ms=detail["end_error_ms"])
            if abs(row["start_signed_ms"]) != row["start_abs_ms"] or abs(row["end_signed_ms"]) != row["end_abs_ms"]:
                raise RuntimeError("Signed endpoints do not reproduce the existing absolute errors")
        endpoints.append(row)
    negative = None
    if record.get("review_status") == "no_chirp":
        first, first_error = group_outcome(tracking, audio, int(rate), candidates, tracking_kwargs)
        negative_observer = ReturnObserver(detection, tracking, benchmark)
        negative_observer.mask_ids = {id(candidate["binary_mask_slice"]): f"candidate:{index}"
                                      for index, candidate in enumerate(candidates)}
        with negative_observer:
            second, second_error = group_outcome(tracking, audio, int(rate), candidates, tracking_kwargs)
        if canonical(first) != canonical(second) or first_error != second_error:
            raise RuntimeError("Instrumentation changed negative tracking/grouping outputs")
        negative = {"file": name, "duration_seconds": len(audio) / int(rate),
            "candidates": len(candidates), "status": "complete" if second_error is None else "pipeline_error",
            "error": second_error, "tracking_rejected": len(second["untracked_candidate_indices"]) if second else None,
            "tracked_candidates": len(candidates) - len(second["untracked_candidate_indices"]) if second else None,
            "accepted_final_trajectories": sum(len(group.ridges) for group in second["groups"]) if second else None,
            "groups": canonical(second["groups"]) if second else None, "tracking_traces": negative_observer.tracks,
            "accepted_curves": [{"source_candidate_indices": list(indices), "points": curve.tolist()}
                                for group in (second["groups"] if second else []) for indices, curve in zip(group.stitched_sources, group.ridges)],
            "scope": "finite_validated_ridges_after_reassignment_tracking_and_chirp_union_not_GUI_biological_acceptance"}
    diagnostic = {"schema": "detection_failure_probe_v1", "equivalence": {
        "benchmark_numeric_and_decisions_exact": True, "candidate_arrays_exact": True,
        "matched_trajectory_arrays_exact": True, "negative_final_arrays_exact": True if negative is not None else None,
        "excluded_fields": ["timings_ms"]}, "sources": observer.source,
        "detector_parameters": observer.parameters, "failures": failures, "endpoints": endpoints,
        "negative": negative, "stages": observer.stages, "raw_extractions": observer.raw,
        "matches": observer.matches}
    return observed, diagnostic
