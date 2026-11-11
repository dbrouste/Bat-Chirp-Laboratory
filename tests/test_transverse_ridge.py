"""Synthetic tests for perpendicular profiles, signed offset and continuity."""
import csv
import json
from types import SimpleNamespace
import wave

import numpy as np
import pytest

from laboratory.transverse_ridge import continuous_normal_ridge, sample_bilinear
from laboratory.acoustic_audit_v2 import evaluate_chirp_v2, run_v2


def test_bilinear_exact_for_linear_time_frequency_surface():
    times=np.arange(0,11,dtype=float)
    freqs=np.arange(20,101,dtype=float)
    z=times[None,:]*2+freqs[:,None]*3
    q=sample_bilinear(times,freqs,z,np.array([1.5,7.25]),np.array([30.5,88.25]))
    assert np.allclose(q,[1.5*2+30.5*3,7.25*2+88.25*3])
    assert np.isnan(sample_bilinear(times,freqs,z,[12.0],[30.0])[0])


def synthetic_grid(offset_khz=4.0, distractor=False):
    times=np.linspace(0,50,501)
    freqs=np.linspace(35,110,751)
    time,frequency=np.meshgrid(times,freqs)
    reference=70.0-2.0*(time-20)
    signal=(40.0*np.exp(-0.5*((frequency-(reference+offset_khz))/0.55)**2)
            *np.exp(-0.5*((time-22)/2.0)**8))
    if distractor:
        signal+=65.0*np.exp(-0.5*((frequency-(reference-7))/0.5)**2) * np.exp(-0.5*((time-22)/0.1)**2)
    # Fixed low noise background.
    return times,freqs,signal


def test_positive_signed_offset_in_downward_chirp():
    times,freq,db=synthetic_grid(offset_khz=4)
    t=np.array([20.0,24.0])
    f=np.array([70.0,62.0])
    result=continuous_normal_ridge(times,freq,db,t,f,count=20,radius_khz=8,step_khz=0.25)
    assert result["median_signed_normal_khz"]>0
    assert result["median_signed_frequency_khz"]>0
    assert result["median_abs_normal_khz"]>1
    assert result["support_fraction_3khz"]<1


def test_negative_signed_offset_and_continuity_under_distractor():
    times,freq,db=synthetic_grid(offset_khz=-3.0,distractor=True)
    t=np.array([20.0,24.0])
    f=np.array([70.0,62.0])
    result=continuous_normal_ridge(times,freq,db,t,f,count=20,radius_khz=8,step_khz=0.25)
    offsets=result["signed_normal_khz"]
    assert np.median(offsets)<0
    assert max(abs(np.diff(offsets)))<=3.0+1e-6


def test_zero_offset_and_invalid_profile():
    times,freq,db=synthetic_grid(offset_khz=0)
    result=continuous_normal_ridge(times,freq,db,[20,24],[70,62],
                                   count=20,radius_khz=8,step_khz=0.25)
    assert abs(result["median_signed_normal_khz"])<=1
    with pytest.raises(ValueError,match="outside spectrogram"):
        continuous_normal_ridge(times,freq,db,[70,75],[70,60])


def make_wav(path):
    path.parent.mkdir(parents=True,exist_ok=True)
    with wave.open(str(path),"wb") as handle:
        handle.setnchannels(1)
        handle.setsampwidth(2)
        handle.setframerate(384000)
        handle.writeframes(b"\0\0"*int(.08*384000))


def chirp():
    return {"source":"manual","status":"validated","chirp_id":"001",
            "points":[{"t_ms":20,"f_khz":70},{"t_ms":24,"f_khz":62}]}


def fake_display(reader, frames, sr, **kwargs):
    assert kwargs["representation"]=="mixed"
    assert kwargs["reassignment_percent"]==35
    assert kwargs["fft_size"]==1024
    assert reader(0,10).shape==(10,)
    t,f,z=synthetic_grid(offset_khz=3.0)
    return SimpleNamespace(times_s=t/1000,
                           frequencies_hz=f*1000,db=z.astype(np.float32))


def test_evaluate_v2_uses_display_pipeline(tmp_path):
    path=tmp_path/"test.wav"
    make_wav(path)
    result=evaluate_chirp_v2(path,chirp(),fake_display)
    assert result["median_signed_frequency_khz"]>0
    assert 0<=result["support_fraction_3khz"]<=1


def test_run_v2_excludes_test_and_emits_queue(tmp_path,monkeypatch):
    import laboratory.acoustic_audit_v2 as module
    root=tmp_path/"dataset"
    manifest=[]
    for split in ("calibration","test"):
        wav=root/split/"wav"/"night"/f"{split}.wav"
        make_wav(wav)
        annotation=root/split/"annotations"/"night"/"night.json"
        annotation.parent.mkdir(parents=True,exist_ok=True)
        annotation.write_text(json.dumps({"files":{wav.name:{"chirps":[chirp()]}}}))
        manifest.append({"partition":split,"session":split,
                         "source_wav":str(wav),"source_json":str(annotation),
                         "dataset_wav":wav.relative_to(root).as_posix(),
                         "dataset_json":annotation.relative_to(root).as_posix()})
    with (root/"manifest.csv").open("w",newline="") as file:
        writer=csv.DictWriter(file,fieldnames=manifest[0].keys())
        writer.writeheader()
        writer.writerows(manifest)
    monkeypatch.setattr(module,"load_display_pipeline",lambda _:fake_display)
    summary=run_v2(root,tmp_path/"results",plot_top=0)
    assert summary["partition"]=="calibration"
    assert summary["chirps_processed"]==1
    assert summary["review_priority_counts"]!={}
    with (tmp_path/"results"/"acoustic_chirps_v2.csv").open(encoding="utf-8-sig") as file:
        rows=list(csv.DictReader(file))
    assert len(rows)==1 and rows[0]["partition"]=="calibration"
    assert rows[0]["source_wav"]==manifest[0]["source_wav"]
