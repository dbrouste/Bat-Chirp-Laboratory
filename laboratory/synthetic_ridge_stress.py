"""Offline synthetic stress matrix for the continuous-normal chirp ridge tracker.

Unlike tests with real WAV files, the exact ridge is analytically known.
The experiments test slope, nearby harmonics and short crossing calls.
"""
from __future__ import annotations
import argparse
import csv
import json
from pathlib import Path
import numpy as np

from .transverse_ridge import continuous_normal_ridge, square_time_scale


def synthetic_case(slope, offset, interference):
    times=np.linspace(18.,26.,401)
    freqs=np.linspace(20.,180.,641)
    t,f=np.meshgrid(times,freqs)
    manual_t=np.array([20.,24.])
    manual_f=150. + slope*(manual_t-20)
    scale,_=square_time_scale(manual_t,manual_f)
    normal_distance=(f-(150.+slope*(t-20)))*scale/np.hypot(scale,slope)
    signal=(32*np.exp(-.5*((normal_distance-offset)/.60)**2)
            *np.exp(-.5*((t-22)/1.85)**8))
    if interference=="parallel":
        signal+=36*np.exp(-.5*((normal_distance-offset-7)/.6)**2)*np.exp(-.5*((t-22)/1.85)**8)
    if interference=="crossing":
        other=150.+offset+slope*(t-20)+16*(t-22)
        signal+=60*np.exp(-.5*((f-other)/.8)**2)*np.exp(-.5*((t-22)/.28)**2)
    if interference=="weak":
        signal=signal*.1
    return times,freqs,signal,manual_t,manual_f


def run_synthetic(output):
    output=Path(output).resolve()
    output.mkdir(parents=True,exist_ok=True)
    rows=[]
    for slope in (0.,-2.,-12.,-30.):
        for offset in (0.,-2.,2.,5.):
            for interference in ("none","parallel","crossing","weak"):
                times,freq,db,manual_t,manual_f=synthetic_case(
                    slope,offset,interference)
                row={"slope_khz_per_ms":slope,
                     "true_signed_normal_khz_eq":offset,
                     "interference":interference,
                     "estimated_signed_normal_khz_eq":"",
                     "absolute_error_khz_eq":"",
                     "direction_correct":"","error":""}
                try:
                    result=continuous_normal_ridge(
                        times,freq,db,manual_t,manual_f,
                        count=20,radius_khz=8,step_khz=.5)
                    estimate=result["median_signed_normal_khz_eq"]
                    row["estimated_signed_normal_khz_eq"]=round(estimate,3)
                    row["absolute_error_khz_eq"]=round(abs(estimate-offset),3)
                    row["direction_correct"]=(np.sign(estimate)==np.sign(offset))
                except (ValueError,FloatingPointError) as exc:
                    row["error"]=str(exc)
                rows.append(row)
    with (output/"synthetic_stress.csv").open("w",encoding="utf-8-sig",newline="") as file:
        writer=csv.DictWriter(file,fieldnames=rows[0].keys())
        writer.writeheader()
        writer.writerows(rows)
    valid=[r for r in rows if not r["error"]]
    mae=float(np.mean([r["absolute_error_khz_eq"] for r in valid])) if valid else None
    summary={
        "version":"synthetic_ridge_stress_v1",
        "cases":len(rows),"evaluated":len(valid),"errors":len(rows)-len(valid),
        "mean_absolute_error_khz_eq":mae,
        "notes":["Synthetic surfaces are not WAVs and do not use STFT 65/35.",
                 "Known ground truth checks geometry and distractor resistance.",
                 "Near-vertical slopes may exceed Nyquist of real recordings.",
                 "Do not use synthetic scores to calibrate real SNR thresholds."],
    }
    (output/"synthetic_summary.json").write_text(
        json.dumps(summary,ensure_ascii=False,indent=2)+"\n",encoding="utf-8")
    return summary


def main(argv=None):
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output",required=True,type=Path)
    args=parser.parse_args(argv)
    print(json.dumps(run_synthetic(args.output),indent=2,ensure_ascii=False))
    return 0


if __name__=="__main__":
    raise SystemExit(main())
