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

FIELDS = (
    "partition", "session", "wav", "annotation", "source_wav", "source_json",
    "chirp_id", "start_ms", "end_ms",
    "median_signed_normal_khz", "median_abs_normal_khz",
    "p90_abs_normal_khz", "median_signed_frequency_khz",
    "support_fraction_3khz", "display_contrast_median_db",
    "time_scale_khz_per_ms", "review_priority", "reason", "error",
)


def evaluate_chirp_v2(wav_path, chirp, display_pipeline, *,
                      fft_size=1024, overlap_fraction=0.75,
                      reassignment_percent=35, spectral_alpha=0.0,
                      max_columns=1600, samples=20, margin_ms=15,
                      radius_khz=8.0, step_khz=0.5,
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
    representation=display_pipeline(
        lambda offset, count: audio[offset:offset+count],
        len(audio), sr, start_time=start/sr,
        fft_size=fft_size, overlap_fraction=overlap_fraction,
        reassignment_percent=reassignment_percent,
        spectral_alpha=spectral_alpha,
        max_columns=max_columns, representation="mixed")
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
        "time_scale_khz_per_ms":round(ridge["time_scale_khz_per_ms"],3),
        "review_priority":priority,
        "reason":";".join(reasons),
        "error":"",
    }
    if not with_plot:
        return row
    return row, (times, frequencies, db, t, f, ridge)


def write_plot(path, info, plot_data):
    """Save a standalone review HTML using the actual blended spectrogram."""
    import plotly.graph_objects as go
    times, freq, db, t, f, ridge=plot_data
    low=max(float(min(f.min(),ridge["ridge_freqs_khz"].min()))-12, 0)
    high=min(float(max(f.max(),ridge["ridge_freqs_khz"].max()))+12,float(freq[-1]))
    keep=(freq>=low)&(freq<=high)
    fig=go.Figure()
    fig.add_trace(go.Heatmap(x=times,y=freq[keep],z=db[keep,:],
                             colorscale="Magma",colorbar=dict(title="dB"),
                             name="STFT 65% + Reassignment 35%"))
    fig.add_trace(go.Scatter(x=t,y=f,mode="lines+markers",
                             name="Annotation manuelle",line=dict(color="cyan",width=2)))
    fig.add_trace(go.Scatter(x=ridge["ridge_times_ms"],y=ridge["ridge_freqs_khz"],
                             mode="lines+markers",name="Crête continue (normales)",
                             line=dict(color="lime",width=2),
                             customdata=np.asarray(ridge["signed_normal_khz"]),
                             hovertemplate="t=%{x:.3f} ms<br>f=%{y:.2f} kHz"
                                          "<br>offset normal=%{customdata:+.2f} kHz<extra></extra>"))
    fig.update_layout(title=(f"{info['wav']} — chirp {info['chirp_id']} — "
                             f"{info['review_priority']}"),
                      xaxis_title="Temps (ms)", yaxis_title="Fréquence (kHz)",
                      template="plotly_dark",height=520)
    fig.update_xaxes(range=[float(t[0])-5,float(t[-1])+5])
    fig.update_yaxes(range=[low,high])
    fig.write_html(str(path),include_plotlyjs=True,full_html=True)


def run_v2(dataset, output, *, analyser_root=DEFAULT_ANALYSER_ROOT,
           max_chirps=None, plot_top=10, fft_size=1024,
           overlap_fraction=0.75, reassignment_percent=35,
           spectral_alpha=0.0, max_columns=1600,
           samples=20, radius_khz=8.0, step_khz=0.5):
    root=Path(dataset).resolve()
    destination=Path(output).resolve()
    if destination==root or destination.is_relative_to(root):
        raise ValueError("output must be outside dataset")
    if max_chirps is not None and max_chirps<1:
        raise ValueError("max_chirps must be positive")
    if plot_top<0:
        raise ValueError("plot_top must be >= 0")
    with (root/"manifest.csv").open(encoding="utf-8-sig",newline="") as stream:
        manifest=list(csv.DictReader(stream))
    if not manifest:
        raise ValueError("empty manifest")
    pipeline=load_display_pipeline(Path(analyser_root))
    cache={}
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
            try:
                row.update(evaluate_chirp_v2(wav,chirp,pipeline,fft_size=fft_size,
                         overlap_fraction=overlap_fraction,
                         reassignment_percent=reassignment_percent,
                         spectral_alpha=spectral_alpha,max_columns=max_columns,
                         samples=samples,radius_khz=radius_khz,step_khz=step_khz))
            except (OSError,ValueError,TypeError,IndexError,RuntimeError) as exc:
                row.update(error=str(exc),review_priority="analysis_error",
                           reason="analysis_error")
                diagnostics.append({"wav":row["wav"],"chirp_id":row["chirp_id"],
                                    "error":str(exc)})
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
    queue=sorted([r for r in results if r["review_priority"] in
                  ("review_first","review_suggested")],
                 key=lambda r:(priority[r["review_priority"]],
                               -float(r["median_abs_normal_khz"])))
    with (destination/"annotation_review_queue.csv").open("w",encoding="utf-8-sig",newline="") as stream:
        writer=csv.DictWriter(stream,fieldnames=FIELDS)
        writer.writeheader()
        writer.writerows(queue)
    # Reprocess just the top N for review images; do not keep all spectrograms
    # (several hundred large 2-D arrays) in RAM.
    plots=[]
    if plot_top:
        lookups={(r["wav"],str(r["chirp_id"])):r for r in queue[:plot_top]}
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
                        step_khz=step_khz,with_plot=True)
                    safe=f"review_{len(plots)+1:03d}.html"
                    write_plot(destination/safe,lookups[match],detail)
                    plots.append({"wav":key,"chirp_id":chirp.get("chirp_id"),
                                  "html":safe})
                except (OSError,ValueError,ImportError) as exc:
                    diagnostics.append({"wav":key,"chirp_id":chirp.get("chirp_id"),
                                        "plot_error":str(exc)})
    summary={
        "version":"continuous_normals_v2",
        "partition":"calibration",
        "representation":"Bat-Acoustic-Analyser mixed display",
        "stft_percent":100-reassignment_percent,
        "reassignment_percent":reassignment_percent,
        "fft_size":fft_size,"overlap_fraction":overlap_fraction,
        "spectral_alpha":spectral_alpha,"samples":samples,
        "normal_radius_khz":radius_khz,"normal_step_khz":step_khz,
        "chirps_processed":len(results),
        "review_priority_counts":dict(Counter(r["review_priority"] for r in results)),
        "queue_count":len(queue),
        "diagnostics":len(diagnostics),
        "plot_count":len(plots),
        "plots":plots,
        "note":"Signed normal offsets are in normalized (scaled time,frequency) geometry; "
               "positive means the ridge is above the manual annotation in frequency. "
               "Thresholds are provisional; no automatic rejection.",
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
    parser.add_argument("--fft-size",type=int,default=1024)
    parser.add_argument("--overlap",type=float,default=0.75)
    parser.add_argument("--reassignment-percent",type=int,default=35)
    parser.add_argument("--spectral-alpha",type=float,default=0.0)
    parser.add_argument("--max-columns",type=int,default=1600)
    parser.add_argument("--normal-radius-khz",type=float,default=8.0)
    parser.add_argument("--normal-step-khz",type=float,default=0.5)
    args=parser.parse_args(argv)
    try:
        result=run_v2(args.dataset,args.output,analyser_root=args.analyser_root,
                      max_chirps=args.max_chirps,plot_top=args.plot_top,
                      fft_size=args.fft_size,overlap_fraction=args.overlap,
                      reassignment_percent=args.reassignment_percent,
                      spectral_alpha=args.spectral_alpha,max_columns=args.max_columns,
                      radius_khz=args.normal_radius_khz,step_khz=args.normal_step_khz)
    except (OSError,ValueError,ImportError) as exc:
        parser.exit(2,f"Error: {exc}\n")
    print(json.dumps(result,indent=2,ensure_ascii=False))
    return 0


if __name__=="__main__":
    raise SystemExit(main())
