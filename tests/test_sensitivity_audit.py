"""Tests of synthetic and corpus reference sensitivity experiments."""
import csv
import json
import wave

import numpy as np
import pytest

from laboratory.sensitivity_audit import (
    variant, scenarios, classify_result, run_sensitivity
)
from laboratory.synthetic_ridge_stress import run_synthetic, synthetic_case
from laboratory.transverse_ridge import continuous_normal_ridge


def example():
    return {"chirp_id":42,"source":"manual","status":"validated",
            "points":[{"t_ms":20.,"f_khz":100.},
                      {"t_ms":24.,"f_khz":68.}]}


@pytest.mark.parametrize("shift",[-5.,-2.,-1.,1.,2.,5.])
def test_global_perturbations_do_not_mutate_source(shift):
    chirp=example()
    old=json.dumps(chirp,sort_keys=True)
    altered=variant(chirp,"global",shift,"all")
    assert json.dumps(chirp,sort_keys=True)==old
    assert altered["points"][0]["f_khz"]==pytest.approx(100+shift)
    assert altered["points"][-1]["f_khz"]==pytest.approx(68+shift)


@pytest.mark.parametrize("part",["start","middle","end"])
def test_local_perturbation_is_local(part):
    changed=variant(example(),"local",5.,part)
    frequencies=np.asarray([p["f_khz"] for p in changed["points"]])
    baseline=np.linspace(100,68,21)
    change=frequencies-baseline
    if part=="start":
        assert change[0]==pytest.approx(5)
        assert change[-1]==pytest.approx(0)
    elif part=="middle":
        assert change[10]==pytest.approx(5)
        assert abs(change[0])<1e-8 and abs(change[-1])<1e-8
    else:
        assert change[0]==pytest.approx(0)
        assert change[-1]==pytest.approx(5)


@pytest.mark.parametrize("part",["start","end","both"])
def test_trim_reduces_time_coverage(part):
    trimmed=variant(example(),"trim",0.4,part)
    t=[p["t_ms"] for p in trimmed["points"]]
    assert t[-1]-t[0]==pytest.approx(2.4)
    assert t[0]>=20 and t[-1]<=24


def test_all_scenarios_present():
    all_cases=list(scenarios())
    assert len(all_cases)==24
    assert set(v for category,v,_ in all_cases if category=="global")=={
        -5.,-2.,-1.,1.,2.,5.}
    assert len([x for x in all_cases if x[0]=="local"])==12
    assert len([x for x in all_cases if x[0]=="trim"])==6


def test_detection_rules_distinguish_truncation():
    a={"median_signed_normal_khz_eq":0,"median_abs_normal_khz_eq":0,
       "p90_abs_normal_khz_eq":0}
    b={"median_signed_normal_khz_eq":-1,"median_abs_normal_khz_eq":1,
       "p90_abs_normal_khz_eq":1}
    assert classify_result(a,b,"global",2)["known_error_detected"]
    assert classify_result(a,b,"local",2)["known_error_detected"]
    assert not classify_result(a,b,"trim",.4)["known_error_detected"]


def fake_display(reader,frames,sr,**kwargs):
    from types import SimpleNamespace
    times=np.linspace(0,80,401)
    freqs=np.linspace(35,140,421)
    t,f=np.meshgrid(times,freqs)
    db=40*np.exp(-.5*((f-(100-8*(t-20)))/1.0)**2)
    return SimpleNamespace(times_s=times/1000,
                           frequencies_hz=freqs*1000,db=db)


def test_sensitivity_uses_only_calibration_and_does_not_edit_json(tmp_path,monkeypatch):
    from laboratory import sensitivity_audit
    monkeypatch.setattr(sensitivity_audit,"load_display_pipeline",lambda _:fake_display)
    dataset=tmp_path/"dataset"
    rows=[]
    for partition in ("calibration","test"):
        wav=dataset/partition/"wav"/partition/f"{partition}.wav"
        wav.parent.mkdir(parents=True)
        with wave.open(str(wav),"wb") as handle:
            handle.setnchannels(1)
            handle.setsampwidth(2)
            handle.setframerate(384000)
            handle.writeframes(b"\0\0"*int(.08*384000))
        ann=dataset/partition/"annotations"/partition/"night.json"
        ann.parent.mkdir(parents=True)
        ann.write_text(json.dumps({"files":{wav.name:{"chirps":[example()]}}}))
        rows.append({"partition":partition,"session":partition,
                     "dataset_wav":wav.relative_to(dataset).as_posix(),
                     "dataset_json":ann.relative_to(dataset).as_posix(),
                     "source_wav":str(wav),"source_json":str(ann)})
    with (dataset/"manifest.csv").open("w",newline="") as file:
        writer=csv.DictWriter(file,fieldnames=rows[0])
        writer.writeheader()
        writer.writerows(rows)
    original=(dataset/rows[0]["dataset_json"]).read_bytes()
    output=tmp_path/"out"
    summary=run_sensitivity(dataset,output,max_chirps=1)
    assert summary["chirps_selected"]==1
    assert summary["cases"]==24
    assert (dataset/rows[0]["dataset_json"]).read_bytes()==original
    with (output/"sensitivity_cases.csv").open(encoding="utf-8-sig") as file:
        entries=list(csv.DictReader(file))
    assert len(entries)==24
    assert all(r["partition"]=="calibration" for r in entries)


def test_synthetic_matrix_run(tmp_path):
    times,freq,db,t,f=synthetic_case(-2,2,"none")
    result=continuous_normal_ridge(times,freq,db,t,f)
    assert result["median_signed_normal_khz_eq"]>0
    summary=run_synthetic(tmp_path/"synthetic")
    assert summary["cases"]==64
    assert (tmp_path/"synthetic"/"synthetic_stress.csv").is_file()
