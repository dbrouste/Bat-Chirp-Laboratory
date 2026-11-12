"""Non-destructive calibration of manual-chirp quality audit sensitivity.

Deliberately perturb a *copy in memory* of calibration-only annotations.
Do not treat recovery of an injected mistake as validation of the
unperturbed human annotation. Never mutate the corpus or original WAVs.
"""
from __future__ import annotations

import argparse
import csv
import json
from collections import Counter, defaultdict
from pathlib import Path

import numpy as np

from .acoustic_audit import DEFAULT_ANALYSER_ROOT, load_display_pipeline, points_of
from .acoustic_audit_v2 import evaluate_chirp_v2

SHIFTS = (-5., -2., -1., 1., 2., 5.)
LOCAL_SHIFTS = (-5., -2., 2., 5.)
LOCAL_AREAS = ("start", "middle", "end")
TRIM_RATIOS = (0.2, 0.4)
RESPONSE_MIN_EQ = 0.5
FIELDS = ("partition","session","wav","source_wav","source_json",
          "chirp_id","scenario","amplitude_khz","region",
          "baseline_signed_eq","perturbed_signed_eq","delta_signed_eq",
          "baseline_median_abs_eq","perturbed_median_abs_eq",
          "baseline_p90_eq","perturbed_p90_eq",
          "baseline_priority","perturbed_priority",
          "baseline_local_score_eq","perturbed_local_score_eq",
          "delta_local_score_eq","baseline_local_flag","perturbed_local_flag",
          "local_new_alert","error",
          "direction_recovered","error_metric_increased",
          "known_error_detected","remarks")


def variant(chirp: dict, name: str, amplitude=0.0, region=""):
    """Return a new chirp with a perturbed polyline, never the original object."""
    ts, fs = points_of(chirp)
    duration = float(ts[-1]-ts[0])
    if duration <= 0:
        raise ValueError("invalid chirp duration")
    # Subdivide each segment so local changes are reproducible, including
    # two-point originals. ~21 nodes are enough for start/middle/end.
    new_t = np.linspace(ts[0],ts[-1],21)
    new_f = np.interp(new_t,ts,fs)
    phase = (new_t-ts[0])/duration
    if name == "global":
        new_f += amplitude
    elif name == "local":
        if region == "start":
            # triangular deformation, peaking at the beginning
            weights = np.clip(1-3*phase,0,1)
        elif region == "middle":
            weights = np.clip(1-abs(phase-.5)/(.25),0,1)
        elif region == "end":
            weights = np.clip(3*phase-2,0,1)
        else:
            raise ValueError(f"invalid region {region}")
        new_f += amplitude*weights
    elif name == "trim":
        # Keep only the central range. These are *missing-coverage* errors,
        # not necessarily detectable from a local perpendicular ridge metric.
        ratio=float(amplitude)
        if not 0 < ratio < 0.5:
            raise ValueError("trim fraction must be in (0,0.5)")
        if region == "start":
            low,high=ratio,1.0
        elif region == "end":
            low,high=0.0,1-ratio
        elif region == "both":
            low,high=ratio/2,1-ratio/2
        else:
            raise ValueError(f"invalid trim region {region}")
        new_t=np.linspace(ts[0]+duration*low,ts[0]+duration*high,21)
        new_f=np.interp(new_t,ts,fs)
    else:
        raise ValueError(f"invalid variant {name!r}")
    updated=dict(chirp)
    updated["points"]=[{"t_ms":float(t),"f_khz":float(f)}
                       for t,f in zip(new_t,new_f)]
    return updated


def scenarios(include_trim=True):
    for shift in SHIFTS:
        yield "global",shift,"all"
    for shift in LOCAL_SHIFTS:
        for region in LOCAL_AREAS:
            yield "local",shift,region
    if include_trim:
        for fraction in TRIM_RATIOS:
            for region in ("start","end","both"):
                yield "trim",fraction,region


def classify_result(original, altered, scenario, amplitude, *,
                    response_min_eq=RESPONSE_MIN_EQ):
    """Sensitivity diagnostics, explicitly separating local vs global claims."""
    base_signed=float(original["median_signed_normal_khz_eq"])
    new_signed=float(altered["median_signed_normal_khz_eq"])
    signed_change=new_signed-base_signed
    abs_increase=(float(altered["median_abs_normal_khz_eq"])
                  -float(original["median_abs_normal_khz_eq"]))
    p90_increase=(float(altered["p90_abs_normal_khz_eq"])
                  -float(original["p90_abs_normal_khz_eq"]))
    if scenario == "global":
        direction = signed_change*amplitude < 0 and abs(signed_change)>=response_min_eq
        detected=direction
    elif scenario == "local":
        # Median signed direction is not reliable if only 20-30% of
        # the curve is wrong; P90 can reveal local departures.
        direction = signed_change*amplitude < 0 and abs(signed_change)>=response_min_eq
        detected=direction or p90_increase>=response_min_eq
    else:
        # A shortened reference can remain exactly on the acoustic ridge:
        # perpendicular errors cannot certify correct start/end coverage.
        direction=False
        detected=False
    return {"delta_signed_eq":round(signed_change,3),
            "direction_recovered":direction,
            "error_metric_increased":abs_increase>=response_min_eq
                                      or p90_increase>=response_min_eq,
            "known_error_detected":detected}


def run_sensitivity(dataset, output, *, analyser_root=DEFAULT_ANALYSER_ROOT,
                    max_chirps=10, skip_chirps=0, fft_size=1024, stft_hop=32,
                    reassignment_hop=16, spectral_alpha=0.0,
                    local_window=5, local_threshold=1.5):
    dataset=Path(dataset).resolve()
    output=Path(output).resolve()
    if output==dataset or output.is_relative_to(dataset):
        raise ValueError("output must be outside the dataset")
    if max_chirps<1:
        raise ValueError("max_chirps must be positive")
    if skip_chirps<0:
        raise ValueError("skip_chirps must be >= 0")
    pipeline=load_display_pipeline(Path(analyser_root))
    with (dataset/"manifest.csv").open(encoding="utf-8-sig",newline="") as file:
        records=list(csv.DictReader(file))
    if not records:
        raise ValueError("empty manifest")
    details=[]
    warnings=[]
    cache={}
    seen=0
    skipped=0
    for entry in records:
        if entry.get("partition")!="calibration":
            continue
        wav=(dataset/entry["dataset_wav"]).resolve()
        ann=(dataset/entry["dataset_json"]).resolve()
        if not wav.is_relative_to(dataset) or not ann.is_relative_to(dataset):
            raise ValueError("path outside dataset")
        if ann not in cache:
            cache[ann]=json.loads(ann.read_text(encoding="utf-8-sig"))
        for chirp in cache[ann]["files"][wav.name].get("chirps",[]):
            if not isinstance(chirp,dict) or chirp.get("source")!="manual" or chirp.get("status")!="validated":
                continue
            if skipped<skip_chirps:
                skipped+=1
                continue
            if seen>=max_chirps:
                break
            seen+=1
            try:
                base=evaluate_chirp_v2(wav,chirp,pipeline,fft_size=fft_size,
                                      stft_hop_samples=stft_hop,
                                      reassignment_hop_samples=reassignment_hop,
                                      spectral_alpha=spectral_alpha,
                                      local_window=local_window,
                                      local_threshold=local_threshold)
            except (OSError,ValueError,TypeError,IndexError,RuntimeError) as exc:
                warnings.append({"wav":entry["dataset_wav"],
                                 "chirp_id":chirp.get("chirp_id"),
                                 "baseline_error":str(exc)})
                continue
            for scenario,amplitude,region in scenarios():
                row={name:"" for name in FIELDS}
                row.update(partition="calibration",session=entry["session"],
                           wav=entry["dataset_wav"],
                           source_wav=entry.get("source_wav",""),
                           source_json=entry.get("source_json",""),
                           chirp_id=chirp.get("chirp_id",""),
                           scenario=scenario,amplitude_khz=amplitude,
                           region=region,
                           baseline_signed_eq=base["median_signed_normal_khz_eq"],
                           baseline_median_abs_eq=base["median_abs_normal_khz_eq"],
                           baseline_p90_eq=base["p90_abs_normal_khz_eq"],
                           baseline_priority=base["review_priority"],
                           baseline_local_score_eq=base["local_max_abs_signed_median_khz_eq"],
                           baseline_local_flag=base["local_flag_experimental"])
                if scenario=="trim":
                    row["remarks"]="Coverage defect; ridge offset alone is not a valid detector"
                try:
                    wrong=variant(chirp,scenario,amplitude,region)
                    result=evaluate_chirp_v2(wav,wrong,pipeline,
                                            fft_size=fft_size,
                                            stft_hop_samples=stft_hop,
                                            reassignment_hop_samples=reassignment_hop,
                                            spectral_alpha=spectral_alpha,
                                            local_window=local_window,
                                            local_threshold=local_threshold)
                    row.update(perturbed_signed_eq=result["median_signed_normal_khz_eq"],
                               perturbed_median_abs_eq=result["median_abs_normal_khz_eq"],
                               perturbed_p90_eq=result["p90_abs_normal_khz_eq"],
                               perturbed_priority=result["review_priority"],
                               perturbed_local_score_eq=result["local_max_abs_signed_median_khz_eq"],
                               delta_local_score_eq=round(
                                   result["local_max_abs_signed_median_khz_eq"]-
                                   base["local_max_abs_signed_median_khz_eq"],3),
                               perturbed_local_flag=result["local_flag_experimental"],
                               local_new_alert=(not base["local_flag_experimental"] and
                                                result["local_flag_experimental"]))
                    row.update(classify_result(base,result,scenario,amplitude))
                except (OSError,ValueError,TypeError,IndexError,RuntimeError) as exc:
                    row["error"]=str(exc)
                details.append(row)
        if seen>=max_chirps:
            break
    if not details:
        raise ValueError("no calibration manual chirps analyzed")
    output.mkdir(parents=True,exist_ok=True)
    with (output/"sensitivity_cases.csv").open("w",encoding="utf-8-sig",newline="") as file:
        writer=csv.DictWriter(file,fieldnames=FIELDS)
        writer.writeheader()
        writer.writerows(details)
    groups=defaultdict(list)
    for row in details:
        groups[row["scenario"]].append(row)
    group_stats={}
    for key,items in groups.items():
        valid=[r for r in items if not r["error"]]
        group_stats[key]={
            "cases":len(items),"valid":len(valid),"errors":len(items)-len(valid),
            "direction_recovered":sum(r["direction_recovered"] for r in valid),
            "detected_heuristic":sum(r["known_error_detected"] for r in valid),
            "legacy_new_alerts":sum(
                r["baseline_priority"]=="no_alert_unverified" and
                r["perturbed_priority"]!="no_alert_unverified" for r in valid),
            "local_new_alerts":sum(r["local_new_alert"] for r in valid),
            "baseline_local_flagged":sum(
                str(r["baseline_local_flag"])=="True" for r in valid),
            "note":("Truncation must be tested with independent time-envelope coverage"
                    if key=="trim" else "Heuristic, non-calibrated sensitivity"),
        }
    summary={
        "version":"reference_sensitivity_v1",
        "partition":"calibration","chirps_selected":seen,
        "chirps_skipped":skipped,
        "chirps_successful":len({(r["wav"],str(r["chirp_id"]))
                                for r in details}),"cases":len(details),
        "fft_size":fft_size,"stft_hop":stft_hop,
        "reassignment_hop":reassignment_hop,
        "local_window":local_window,
        "local_threshold_experimental_khz_eq":local_threshold,
        "groups":group_stats,"baseline_errors":warnings,
        "notes":["All perturbations of manual coordinates are in-memory only.",
                 "Global shifts: direction response; local shifts: sign or P90 response.",
                 "Truncation is intentionally NOT scored as detected by perpendicular error.",
                 "High detection of synthetic shifts does not imply manual reference accuracy.",
                 "Use independent energy/onset/offset model before validating completeness."],
    }
    (output/"sensitivity_summary.json").write_text(
        json.dumps(summary,ensure_ascii=False,indent=2)+"\n",encoding="utf-8")
    return summary


def main(argv=None):
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--dataset",type=Path,required=True)
    parser.add_argument("--output",type=Path,required=True)
    parser.add_argument("--analyser-root",type=Path,default=DEFAULT_ANALYSER_ROOT)
    parser.add_argument("--max-chirps",type=int,default=10)
    parser.add_argument("--skip-chirps",type=int,default=0,
                        help="Skip the first N validated manual calibration chirps")
    parser.add_argument("--fft-size",type=int,default=1024)
    parser.add_argument("--stft-hop",type=int,default=32)
    parser.add_argument("--reassignment-hop",type=int,default=16)
    parser.add_argument("--spectral-alpha",type=float,default=0.0)
    parser.add_argument("--local-window",type=int,default=5)
    parser.add_argument("--local-threshold",type=float,default=1.5)
    args=parser.parse_args(argv)
    try:
        result=run_sensitivity(args.dataset,args.output,
                               analyser_root=args.analyser_root,
                               max_chirps=args.max_chirps,
                               skip_chirps=args.skip_chirps,fft_size=args.fft_size,
                               stft_hop=args.stft_hop,
                               reassignment_hop=args.reassignment_hop,
                               spectral_alpha=args.spectral_alpha,
                               local_window=args.local_window,
                               local_threshold=args.local_threshold)
    except (OSError,ValueError,ImportError,KeyError) as exc:
        parser.exit(2,f"Error: {exc}\n")
    print(json.dumps(result,ensure_ascii=False,indent=2))
    return 0


if __name__=="__main__":
    raise SystemExit(main())
