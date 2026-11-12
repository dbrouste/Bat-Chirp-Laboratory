"""Second-generation manual annotation audit: continuous normal ridge + HTML plots.

Uses the original 65% STFT / 35% Reassignment GUI display via the
Bat-Acoustic-Analyser checkout; leaves manual annotations and WAVs untouched.
The test partition is completely excluded.
"""
from __future__ import annotations

import argparse
from collections import Counter
import csv
import json
from pathlib import Path

import numpy as np
import soundfile as sf

from .acoustic_audit import DEFAULT_ANALYSER_ROOT, load_display_pipeline, points_of
from .transverse_ridge import continuous_normal_ridge
from .local_quality import local_error_metrics, experimental_local_flag
from .quality_audit import inspect_chirp
from .reference_suspicion import suspicion_metrics, SCORE_VERSION

FIELDS = (
    "partition", "session", "wav", "annotation", "source_wav", "source_json",
    "chirp_id", "start_ms", "end_ms",
    "median_signed_normal_khz", "median_abs_normal_khz",
    "p90_abs_normal_khz", "median_signed_frequency_khz",
    "support_fraction_3khz", "display_contrast_median_db",
    "time_scale_khz_per_ms", "normalization_mode",
    "median_signed_normal_khz_eq", "median_abs_normal_khz_eq",
    "p90_abs_normal_khz_eq", "support_fraction_3khz_eq",
    "local_window_points", "local_max_median_abs_khz_eq",
    "local_max_abs_signed_median_khz_eq", "local_peak_signed_median_khz_eq",
    "local_peak_start_index", "local_peak_end_index", "local_peak_region",
    "local_start_median_abs_khz_eq", "local_middle_median_abs_khz_eq",
    "local_end_median_abs_khz_eq", "local_flag_experimental",
    "local_signed_windows_json", "local_abs_windows_json",
    "review_priority", "reason", "error",
    "suspicion_score", "suspicion_reason", "suspicion_reliability",
    "suspicion_breakdown_json", "suspicion_score_version", "quality_flags",
)


def evaluate_chirp_v2(wav_path, chirp, display_pipeline, *,
                      fft_size=1024, overlap_fraction=0.75,
                      reassignment_percent=35, spectral_alpha=0.0,
                      max_columns=1600, samples=20, margin_ms=15,
                      radius_khz=8.0, step_khz=0.5,
                      stft_hop_samples=32, reassignment_hop_samples=16,
                      local_window=5, local_threshold=1.5,
                      with_plot=False):
    """Evaluate one curve and optionally return plot-ready spectrogram data."""
    t, f = points_of(chirp)
    if not 0 <= reassignment_percent <= 100 or not 0 <= spectral_alpha <= 4:
        raise ValueError("invalid spectral settings")
    with sf.SoundFile(str(wav_path)) as wav:
        sr=wav.samplerate
        if t[0]<0 or t[-1]>1000*len(wav)/sr:
            raise ValueError("annotation outside WAV")
        start=max(0,int((t[0]-margin_ms)*sr/1000))
        stop=min(len(wav),int(np.ceil((t[-1]+margin_ms)*sr/1000)))
        wav.seek(start)
        audio=wav.read(stop-start,dtype="float32",always_2d=True).mean(axis=1)
    # GUI's mixed result always uses the STFT time axis. At the old
    # 1024/75% setting this was 256 samples (~0.67 ms at 384 kHz),
    # irrespective of a much finer reassignment grid. Force a fine STFT
    # grid and a matching fine reassignment hop, retaining the EXACT GUI
    # blend implementation (including linear-magnitude mixing).
    if (not isinstance(stft_hop_samples, int) or stft_hop_samples < 1
            or stft_hop_samples > fft_size):
        raise ValueError("stft_hop_samples must be in 1..fft_size")
    if (not isinstance(reassignment_hop_samples, int)
            or not 1 <= reassignment_hop_samples <= 512):
        raise ValueError("reassignment_hop_samples must be in 1..512")
    if not 0 <= overlap_fraction < 1:
        raise ValueError("overlap_fraction must be in [0,1)")
    requested_stft_hop = max(1, int(round(fft_size*(1-overlap_fraction))))
    effective_stft_hop = min(stft_hop_samples, requested_stft_hop)
    effective_overlap = 1.0 - effective_stft_hop/fft_size
    # Explicit reassignment hop can exceed the GUI's default 1600
    # column budget. Increase the column budget for the selected short
    # excerpt rather than silently increasing the reassignment hop.
    reassigned_frames = 1 + max(0, (max(len(audio),512)-512) // reassignment_hop_samples)
    required_columns = max(max_columns, reassigned_frames)
    if required_columns > 10000:
        raise ValueError("excerpt too long for the selected fine hop; reduce margin or increase hop")
    representation=display_pipeline(
        lambda offset, count: audio[offset:offset+count],
        len(audio), sr, start_time=start/sr,
        fft_size=fft_size, overlap_fraction=effective_overlap,
        reassignment_percent=reassignment_percent,
        reassignment_hop_samples=reassignment_hop_samples,
        spectral_alpha=spectral_alpha,
        max_columns=required_columns, representation="mixed")
    times=np.asarray(representation.times_s,dtype=float)*1000
    frequencies=np.asarray(representation.frequencies_hz,dtype=float)/1000
    db=np.asarray(representation.db,dtype=float)
    if db.shape!=(len(frequencies),len(times)):
        raise ValueError("display grid does not match axes")
    off=(times<t[0]-2) | (times>t[-1]+2)
    if off.sum()<6:
        raise ValueError("insufficient off-chirp frames for baseline")
    baseline=np.percentile(db[:,off],50,axis=1)
    ridge=continuous_normal_ridge(times,frequencies,db,t,f,
                                  count=samples,radius_khz=radius_khz,
                                  step_khz=step_khz,noise_db=baseline)
    local=local_error_metrics(ridge["signed_normal_khz_eq"],window=local_window)
    local_flag=experimental_local_flag(local,threshold=local_threshold)
    from .transverse_ridge import sample_bilinear
    ridge_db=sample_bilinear(times,frequencies,db,
                            ridge["ridge_times_ms"],ridge["ridge_freqs_khz"])
    background=np.interp(ridge["ridge_freqs_khz"],frequencies,baseline)
    contrast=float(np.median(ridge_db-background))
    mean_offset=abs(ridge["median_signed_normal_khz"])
    median_error=ridge["median_abs_normal_khz"]
    p90_error=ridge["p90_abs_normal_khz"]
    support=ridge["support_fraction_3khz"]
    reasons=[]
    if median_error>3:
        reasons.append("median_normal_error_gt_3khz")
    if p90_error>5:
        reasons.append("p90_normal_error_gt_5khz")
    if mean_offset>2:
        reasons.append("systematic_signed_offset")
    if contrast<6:
        reasons.append("weak_display_contrast")
    priority="review_first" if len(reasons)>=2 or median_error>4 else (
        "review_suggested" if reasons else "no_alert_unverified")
    row={
        "start_ms":round(float(t[0]),5),
        "end_ms":round(float(t[-1]),5),
        "median_signed_normal_khz":round(ridge["median_signed_normal_khz"],3),
        "median_abs_normal_khz":round(median_error,3),
        "p90_abs_normal_khz":round(p90_error,3),
        "median_signed_frequency_khz":round(ridge["median_signed_frequency_khz"],3),
        "support_fraction_3khz":round(support,3),
        "display_contrast_median_db":round(contrast,2),
        "time_scale_khz_per_ms":round(ridge["time_scale_khz_per_ms"],6),
        "normalization_mode":ridge["normalization_mode"],
        "median_signed_normal_khz_eq":round(ridge["median_signed_normal_khz_eq"],3),
        "median_abs_normal_khz_eq":round(ridge["median_abs_normal_khz_eq"],3),
        "p90_abs_normal_khz_eq":round(ridge["p90_abs_normal_khz_eq"],3),
        "support_fraction_3khz_eq":round(ridge["support_fraction_3khz_eq"],3),
        "review_priority":priority,
        "reason":";".join(reasons),
        "local_window_points":local_window,
        "local_max_median_abs_khz_eq":round(local["local_max_median_abs_khz_eq"],3),
        "local_max_abs_signed_median_khz_eq":round(local["local_max_abs_signed_median_khz_eq"],3),
        "local_peak_signed_median_khz_eq":round(local["local_peak_signed_median_khz_eq"],3),
        "local_peak_start_index":local["local_peak_start_index"],
        "local_peak_end_index":local["local_peak_end_index"],
        "local_peak_region":local["local_peak_region"],
        "local_start_median_abs_khz_eq":round(local["local_start_median_abs_khz_eq"],3),
        "local_middle_median_abs_khz_eq":round(local["local_middle_median_abs_khz_eq"],3),
        "local_end_median_abs_khz_eq":round(local["local_end_median_abs_khz_eq"],3),
        "local_flag_experimental":local_flag,
        "local_signed_windows_json":json.dumps([
            round(float(x),3) for x in local["local_signed_window_series_khz_eq"]]),
        "local_abs_windows_json":json.dumps([
            round(float(x),3) for x in local["local_abs_window_series_khz_eq"]]),
        "error":"",
    }
    if not with_plot:
        return row
    display_metadata = {
        "stft_hop_samples": effective_stft_hop,
        "stft_hop_us": 1_000_000*effective_stft_hop/sr,
        "reassignment_hop_samples": reassignment_hop_samples,
        "reassignment_hop_us": 1_000_000*reassignment_hop_samples/sr,
        "stft_columns": len(times),
        "max_columns": required_columns,
    }
    return row, (times, frequencies, db, t, f, ridge, display_metadata)


def write_plot(path, info, plot_data, *, detail="both"):
    """Interactive stand-alone GUI-blend spectrogram and/or normal profiles."""
    import plotly.graph_objects as go
    from plotly.subplots import make_subplots
    if detail not in ("overlay", "profiles", "both"):
        raise ValueError("detail must be overlay, profiles, or both")
    times, freq, db, t, f, ridge, grid = plot_data
    show_overlay = detail != "profiles"
    show_profiles = detail != "overlay"
    rows = int(show_overlay) + int(show_profiles)
    titles = (["Spectrogramme mixte + trajectoires"] if show_overlay else [])
    titles += (["Profils perpendiculaires : intensité et chemin retenu (kHz_eq)"]
               if show_profiles else [])
    fig = make_subplots(rows=rows, cols=1, subplot_titles=titles,
                        vertical_spacing=0.11 if rows == 2 else 0.05)
    low = max(float(min(f.min(), ridge["ridge_freqs_khz"].min()))-12, 0)
    high = min(float(max(f.max(), ridge["ridge_freqs_khz"].max()))+12,
               float(freq[-1]))
    if show_overlay:
        keep = (freq>=low)&(freq<=high)
        fig.add_trace(go.Heatmap(
            x=times,y=freq[keep],z=db[keep,:],colorscale="Magma",
            colorbar=dict(title="dB",x=1.02,len=0.42 if rows == 2 else 0.8,
                          y=0.79 if rows == 2 else 0.5),
            name="STFT 65 % + Reassignment 35 %"),row=1,col=1)
        fig.add_trace(go.Scatter(
            x=t,y=f,mode="lines+markers",name="Annotation manuelle",
            line=dict(color="cyan",width=2)),row=1,col=1)
        fig.add_trace(go.Scatter(
            x=ridge["ridge_times_ms"],y=ridge["ridge_freqs_khz"],
            mode="lines+markers",name="Crête continue (normales)",
            line=dict(color="lime",width=2),
            customdata=np.asarray(ridge["signed_normal_khz"]),
            hovertemplate="t=%{x:.3f} ms<br>f=%{y:.2f} kHz"
                          "<br>écart normal=%{customdata:+.2f} kHz_eq<extra></extra>"),
            row=1,col=1)
        fig.update_xaxes(range=[float(t[0])-5,float(t[-1])+5],
                         title_text="Temps (ms)",row=1,col=1)
        fig.update_yaxes(range=[low,high],
                         title_text="Fréquence (kHz)",row=1,col=1)
    if show_profiles:
        row_index = 2 if show_overlay else 1
        offsets = np.asarray(ridge["normal_offsets_khz"])
        profile = np.asarray(ridge["profiles_db"],dtype=float)
        # Normalize each profile only for visualization, not the tracking
        # score; these values describe the display, not calibrated SNR.
        baseline = np.nanmin(profile,axis=1)
        contrast = np.where(np.isfinite(profile),
                            profile-baseline[:,None],np.nan)
        fig.add_trace(go.Heatmap(
            x=ridge["reference_times_ms"],y=offsets,z=contrast.T,
            colorscale="Viridis",colorbar=dict(
                title="dB relatif",x=1.02,len=0.42 if rows == 2 else 0.8,
                y=0.23 if rows == 2 else 0.5),
            name="Profils normaux"),row=row_index,col=1)
        fig.add_trace(go.Scatter(
            x=ridge["reference_times_ms"],y=ridge["signed_normal_khz"],
            mode="lines+markers",name="Chemin retenu (écart signé)",
            line=dict(color="white",width=2),
            hovertemplate="t=%{x:.3f} ms<br>écart normal=%{y:+.2f} kHz_eq<extra></extra>"),
            row=row_index,col=1)
        fig.add_hline(y=0,line_color="cyan",line_dash="dash",
                      row=row_index,col=1)
        fig.update_xaxes(title_text="Temps de référence (ms)",
                         row=row_index,col=1)
        fig.update_yaxes(title_text="Écart normal signé (kHz_eq)",
                         row=row_index,col=1)
    fig.update_layout(
        title=(f"{info['wav']} — chirp {info['chirp_id']} — "
               f"{info['review_priority']}<br><sup>"
               f"STFT hop {grid['stft_hop_samples']} "
               f"({grid['stft_hop_us']:.0f} µs) · "
               f"Reassignment hop {grid['reassignment_hop_samples']} "
               f"({grid['reassignment_hop_us']:.0f} µs) · "
               f"{grid['stft_columns']} colonnes</sup>"),
        template="plotly_dark",height=850 if rows == 2 else 560,
        legend=dict(orientation="h",y=-0.13))
    fig.write_html(str(path),include_plotlyjs=True,full_html=True)


def run_v2(dataset, output, *, analyser_root=DEFAULT_ANALYSER_ROOT,
           max_chirps=None, plot_top=10, plot_mode="suspects",
           plot_detail="both", validate_shifts=False, shift_limit=10,
           fft_size=1024,
           overlap_fraction=0.75, reassignment_percent=35,
           spectral_alpha=0.0, max_columns=1600,
           stft_hop_samples=32, reassignment_hop_samples=16,
           samples=20, radius_khz=8.0, step_khz=0.5,
           local_window=5, local_threshold=1.5):
    root=Path(dataset).resolve()
    destination=Path(output).resolve()
    if destination==root or destination.is_relative_to(root):
        raise ValueError("output must be outside dataset")
    if max_chirps is not None and max_chirps<1:
        raise ValueError("max_chirps must be positive")
    if plot_top<0:
        raise ValueError("plot_top must be >= 0")
    if plot_mode not in ("suspects","validation"):
        raise ValueError("plot_mode must be suspects or validation")
    if plot_detail not in ("overlay","profiles","both"):
        raise ValueError("plot_detail must be overlay, profiles or both")
    if shift_limit<1:
        raise ValueError("shift_limit must be >= 1")
    if local_window < 3 or local_window > samples or local_threshold <= 0:
        raise ValueError("invalid local scoring parameters")
    with (root/"manifest.csv").open(encoding="utf-8-sig",newline="") as stream:
        manifest=list(csv.DictReader(stream))
    if not manifest:
        raise ValueError("empty manifest")
    pipeline=load_display_pipeline(Path(analyser_root))
    cache={}
    wav_headers={}
    results=[]
    diagnostics=[]
    for entry in manifest:
        if entry.get("partition")!="calibration":
            continue
        wav=(root/entry["dataset_wav"]).resolve()
        ann=(root/entry["dataset_json"]).resolve()
        if not wav.is_relative_to(root) or not ann.is_relative_to(root):
            raise ValueError("path outside dataset")
        try:
            if ann not in cache:
                cache[ann]=json.loads(ann.read_text(encoding="utf-8-sig"))
            record=cache[ann]["files"][wav.name]
            chirps=record["chirps"]
            if not isinstance(chirps,list):
                raise ValueError("chirps not a list")
        except (OSError,ValueError,KeyError,TypeError) as exc:
            diagnostics.append({"wav":entry["dataset_wav"],"error":str(exc)})
            continue
        for chirp in chirps:
            if not isinstance(chirp,dict) or chirp.get("source")!="manual" or chirp.get("status")!="validated":
                continue
            if max_chirps is not None and len(results)>=max_chirps:
                break
            row={key:"" for key in FIELDS}
            row.update({k:entry.get(k,"") for k in ("partition","session",
                                                    "source_wav","source_json")})
            row.update(wav=entry["dataset_wav"],annotation=entry["dataset_json"],
                       chirp_id=chirp.get("chirp_id",""))
            if wav not in wav_headers:
                try:
                    info = sf.info(str(wav))
                    wav_headers[wav] = (info.duration, info.samplerate)
                except (OSError, RuntimeError, ValueError):
                    wav_headers[wav] = (None, None)
            duration_s, sample_rate = wav_headers[wav]
            geometry_flags = inspect_chirp(chirp, duration_s, sample_rate)["flags"]
            try:
                row.update(evaluate_chirp_v2(wav,chirp,pipeline,fft_size=fft_size,
                         overlap_fraction=overlap_fraction,
                         reassignment_percent=reassignment_percent,
                         spectral_alpha=spectral_alpha,max_columns=max_columns,
                         samples=samples,radius_khz=radius_khz,step_khz=step_khz,
                         stft_hop_samples=stft_hop_samples,
                         reassignment_hop_samples=reassignment_hop_samples,
                         local_window=local_window,local_threshold=local_threshold))
            except (OSError,ValueError,TypeError,IndexError,RuntimeError) as exc:
                row.update(error=str(exc),review_priority="analysis_error",
                           reason="analysis_error")
                diagnostics.append({"wav":row["wav"],"chirp_id":row["chirp_id"],
                                    "error":str(exc)})
            row.update(suspicion_metrics(row, geometry_flags))
            results.append(row)
        if max_chirps is not None and len(results)>=max_chirps:
            break
    if not results:
        raise ValueError("no calibration manual chirps")
    destination.mkdir(parents=True,exist_ok=True)
    with (destination/"acoustic_chirps_v2.csv").open("w",encoding="utf-8-sig",newline="") as stream:
        writer=csv.DictWriter(stream,fieldnames=FIELDS)
        writer.writeheader()
        writer.writerows(results)
    priority={"review_first":0,"review_suggested":1,"no_alert_unverified":2,
              "analysis_error":3}
    def review_sort_key(row):
        # Numeric scores first; invalid analyses go last rather than being
        # misrepresented as confirmed annotation errors.
        raw = row["suspicion_score"]
        return (raw == "", -float(raw) if raw != "" else 0,
                priority.get(row["review_priority"], 9),
                str(row["wav"]), str(row["chirp_id"]))

    ranked = sorted(results, key=review_sort_key)
    with (destination/"reference_review_ranked.csv").open(
            "w", encoding="utf-8-sig", newline="") as stream:
        writer = csv.DictWriter(stream, fieldnames=FIELDS)
        writer.writeheader()
        writer.writerows(ranked)
    queue = sorted((r for r in results if r["review_priority"] in
                    ("review_first", "review_suggested")), key=review_sort_key)
    with (destination/"annotation_review_queue.csv").open("w",encoding="utf-8-sig",newline="") as stream:
        writer=csv.DictWriter(stream,fieldnames=FIELDS)
        writer.writeheader()
        writer.writerows(queue)
    # Reprocess just the top N for review images; do not keep all spectrograms
    # (several hundred large 2-D arrays) in RAM.
    plots=[]
    if plot_top:
        # Validation mode must display apparently correct annotations as well:
        # an anchored tracker can look convincing without measuring the ridge.
        candidates = (results if plot_mode == "validation" else queue)
        candidates = [r for r in candidates if not r["error"]][:plot_top]
        lookups={(r["wav"],str(r["chirp_id"])):r for r in candidates}
        plotted=set()
        for entry in manifest:
            if not lookups or entry.get("partition")!="calibration":
                continue
            key=entry["dataset_wav"]
            if not any(item[0]==key for item in lookups):
                continue
            wav=(root/key).resolve()
            ann=(root/entry["dataset_json"]).resolve()
            for chirp in cache[ann]["files"][wav.name]["chirps"]:
                match=(key,str(chirp.get("chirp_id","")))
                if match not in lookups or match in plotted:
                    continue
                plotted.add(match)
                try:
                    _, detail=evaluate_chirp_v2(wav,chirp,pipeline,
                        fft_size=fft_size,overlap_fraction=overlap_fraction,
                        reassignment_percent=reassignment_percent,
                        spectral_alpha=spectral_alpha,max_columns=max_columns,
                        samples=samples,radius_khz=radius_khz,
                        step_khz=step_khz,
                        stft_hop_samples=stft_hop_samples,
                        reassignment_hop_samples=reassignment_hop_samples,
                        local_window=local_window,local_threshold=local_threshold,
                        with_plot=True)
                    safe=f"review_{len(plots)+1:03d}.html"
                    write_plot(destination/safe,lookups[match],detail,
                               detail=plot_detail)
                    plots.append({"wav":key,"chirp_id":chirp.get("chirp_id"),
                                  "html":safe})
                except (OSError,ValueError,ImportError) as exc:
                    diagnostics.append({"wav":key,"chirp_id":chirp.get("chirp_id"),
                                        "plot_error":str(exc)})
    shift_rows = []
    if validate_shifts:
        # Deliberately perturb manual references in memory only. The original
        # annotation is *not* assumed perfect: compare recovery relative to
        # baseline before and after the injected displacement.
        selected = [r for r in results if not r["error"]][:shift_limit]
        selected_keys = {(r["wav"], str(r["chirp_id"])) for r in selected}
        for entry in manifest:
            if entry.get("partition") != "calibration":
                continue
            key = entry["dataset_wav"]
            if not any(k[0] == key for k in selected_keys):
                continue
            wav = (root/key).resolve()
            ann = (root/entry["dataset_json"]).resolve()
            for chirp in cache[ann]["files"][wav.name]["chirps"]:
                match = (key, str(chirp.get("chirp_id","")))
                if match not in selected_keys:
                    continue
                baseline = next(r for r in selected if
                                (r["wav"],str(r["chirp_id"])) == match)
                for shift in (3.0,5.0,-5.0):
                    # No files are ever written; shifted points exist in
                    # memory for this diagnostic experiment only.
                    perturbed = dict(chirp)
                    perturbed["points"] = [
                        {**p, "f_khz": float(p["f_khz"])+shift}
                        for p in chirp["points"]]
                    record = {
                        "wav":key, "chirp_id":chirp.get("chirp_id"),
                        "injected_vertical_shift_khz":shift,
                        "baseline_signed_normal":baseline["median_signed_normal_khz"],
                        "shifted_signed_normal":"", "recovery_signed_delta":"",
                        "opposite_direction_recovered":False,
                        "error":""}
                    try:
                        shifted = evaluate_chirp_v2(
                            wav,perturbed,pipeline,fft_size=fft_size,
                            overlap_fraction=overlap_fraction,
                            reassignment_percent=reassignment_percent,
                            spectral_alpha=spectral_alpha,
                            max_columns=max_columns,samples=samples,
                            radius_khz=radius_khz,step_khz=step_khz,
                            stft_hop_samples=stft_hop_samples,
                            reassignment_hop_samples=reassignment_hop_samples)
                        current = shifted["median_signed_normal_khz"]
                        delta = current - float(baseline["median_signed_normal_khz"])
                        record["shifted_signed_normal"] = current
                        record["recovery_signed_delta"] = round(delta,3)
                        record["opposite_direction_recovered"] = (
                            delta * shift < 0 and abs(delta)>=0.5)
                    except (OSError,ValueError,TypeError,IndexError,RuntimeError) as exc:
                        record["error"] = str(exc)
                    shift_rows.append(record)
        with (destination/"shift_validation.csv").open(
                "w",encoding="utf-8-sig",newline="") as stream:
            fields = ("wav","chirp_id","injected_vertical_shift_khz",
                      "baseline_signed_normal","shifted_signed_normal",
                      "recovery_signed_delta","opposite_direction_recovered",
                      "error")
            writer = csv.DictWriter(stream,fieldnames=fields)
            writer.writeheader()
            writer.writerows(shift_rows)
    evaluated_shifts=[r for r in shift_rows if not r["error"]]
    shift_detection_rate = (
        sum(r["opposite_direction_recovered"] for r in evaluated_shifts)
        / len(evaluated_shifts) if evaluated_shifts else None)
    summary={
        "version":"continuous_normals_square_v3",
        "partition":"calibration",
        "representation":"Bat-Acoustic-Analyser mixed display",
        "stft_percent":100-reassignment_percent,
        "reassignment_percent":reassignment_percent,
        "fft_size":fft_size,"overlap_fraction_minimum":overlap_fraction,
        "stft_hop_samples_maximum":stft_hop_samples,
        "reassignment_hop_samples":reassignment_hop_samples,
        "spectral_alpha":spectral_alpha,"samples":samples,
        "normal_radius_khz":radius_khz,"normal_step_khz":step_khz,
        "chirps_processed":len(results),
        "review_priority_counts":dict(Counter(r["review_priority"] for r in results)),
        "queue_count":len(queue),
        "ranked_count":len(ranked),
        "suspicion_score_version":SCORE_VERSION,
        "suspicion_score_calibrated":False,
        "diagnostics":len(diagnostics),
        "local_window_points":local_window,
        "local_threshold_experimental_khz_eq":local_threshold,
        "local_flags_experimental":sum(r.get("local_flag_experimental") is True for r in results),
        "plot_mode":plot_mode,
        "plot_detail":plot_detail,
        "plot_count":len(plots),
        "plots":plots,
        "shift_validation_runs":len(shift_rows),
        "shift_validation_detection_rate":shift_detection_rate,
        "normalization":"square from the manual frequency and time spans; "
                        "10 kHz/ms fallback if F/T < 1; independent of GUI zoom",
        "note":"Normal distances and thresholds are in geometric kHz_eq, "
               "not vertical frequency differences. Legacy *_normal_khz "
               "fields also mean kHz_eq. Historical scores are not directly "
               "comparable; thresholds remain provisional.",
    }
    (destination/"acoustic_summary_v2.json").write_text(
        json.dumps(summary,indent=2,ensure_ascii=False)+"\n",encoding="utf-8")
    (destination/"acoustic_diagnostics_v2.json").write_text(
        json.dumps(diagnostics,indent=2,ensure_ascii=False)+"\n",encoding="utf-8")
    return summary


def main(argv=None):
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--dataset",type=Path,required=True)
    parser.add_argument("--output",type=Path,required=True)
    parser.add_argument("--analyser-root",type=Path,default=DEFAULT_ANALYSER_ROOT)
    parser.add_argument("--max-chirps",type=int)
    parser.add_argument("--plot-top",type=int,default=10)
    parser.add_argument("--plot-mode",choices=("suspects","validation"),
                        default="suspects",
                        help="validation exports first N chirps, including no-alert")
    parser.add_argument("--plot-detail",choices=("overlay","profiles","both"),
                        default="both")
    parser.add_argument("--validate-shifts",action="store_true",
                        help="Inject +3,+5,-5 kHz errors in memory and compare recovery")
    parser.add_argument("--shift-limit",type=int,default=10)
    parser.add_argument("--fft-size",type=int,default=1024)
    parser.add_argument("--overlap",type=float,default=0.75,
                        help="Minimum STFT overlap; fine hop is additionally enforced")
    parser.add_argument("--stft-hop",type=int,default=32,
                        help="Maximum STFT hop in samples (default 32)")
    parser.add_argument("--reassignment-hop",type=int,default=16,
                        help="Explicit reassignment hop in samples (default 16)")
    parser.add_argument("--reassignment-percent",type=int,default=35)
    parser.add_argument("--spectral-alpha",type=float,default=0.0)
    parser.add_argument("--max-columns",type=int,default=1600)
    parser.add_argument("--local-window",type=int,default=5)
    parser.add_argument("--local-threshold",type=float,default=1.5)
    parser.add_argument("--normal-radius-khz",type=float,default=8.0)
    parser.add_argument("--normal-step-khz",type=float,default=0.5)
    args=parser.parse_args(argv)
    try:
        result=run_v2(args.dataset,args.output,analyser_root=args.analyser_root,
                      max_chirps=args.max_chirps,plot_top=args.plot_top,
                      plot_mode=args.plot_mode,plot_detail=args.plot_detail,
                      validate_shifts=args.validate_shifts,
                      shift_limit=args.shift_limit,
                      fft_size=args.fft_size,overlap_fraction=args.overlap,
                      reassignment_percent=args.reassignment_percent,
                      spectral_alpha=args.spectral_alpha,max_columns=args.max_columns,
                      radius_khz=args.normal_radius_khz,step_khz=args.normal_step_khz,
                      stft_hop_samples=args.stft_hop,
                      reassignment_hop_samples=args.reassignment_hop,
                      local_window=args.local_window,
                      local_threshold=args.local_threshold)
    except (OSError,ValueError,ImportError) as exc:
        parser.exit(2,f"Error: {exc}\n")
    print(json.dumps(result,indent=2,ensure_ascii=False))
    return 0


if __name__=="__main__":
    raise SystemExit(main())
