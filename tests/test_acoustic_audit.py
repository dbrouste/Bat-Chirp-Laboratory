"""Tests for trajectory acoustic support, errors, and test isolation."""
import csv
import json
import wave

import numpy as np
import pytest

from laboratory.acoustic_audit import evaluate_chirp, run


def chirp(points=None):
    return {"source":"manual","status":"validated","chirp_id":1,
            "points":points or [{"t_ms":20,"f_khz":80},{"t_ms":24,"f_khz":60}]}


def create_wav(path, sr=384000):
    path.parent.mkdir(parents=True,exist_ok=True)
    n=np.arange(int(sr*0.08))
    x=np.sin(2*np.pi*70000*n/sr)*np.exp(-((n/sr-0.022)/0.003)**2)
    pcm=(x*10000).astype("<i2")
    with wave.open(str(path),"wb") as handle:
        handle.setnchannels(1)
        handle.setsampwidth(2)
        handle.setframerate(sr)
        handle.writeframes(pcm.tobytes())


def fake_reassignment(y,sr,**kwargs):
    n_fft=kwargs["n_fft"]
    hop=kwargs["hop"]
    freq=np.fft.rfftfreq(n_fft,1/sr)
    selected=(freq>=kwargs["fmin"])&(freq<=kwargs["fmax"])
    freq=freq[selected]
    times=(np.arange(0,len(y)-n_fft+1,hop)+(n_fft-1)/2)/sr
    power=np.ones((len(freq),len(times)))
    center=np.argmin(abs(freq-70000))
    power[center,:]=10000
    return {"freqs_hz":freq,"times_s":times,
            "reassigned_power":power,"stft_power":power}


def test_evaluate_chirp_returns_finite_metrics(tmp_path):
    wav=tmp_path/"test.wav"
    create_wav(wav)
    row=evaluate_chirp(wav,chirp(),fake_reassignment)
    assert row["samples_evaluated"]==20
    assert np.isfinite(row["snr_median_db"])
    assert 0<=row["support_fraction_3khz"]<=1


def test_invalid_points_rejected(tmp_path):
    wav=tmp_path/"test.wav"
    create_wav(wav)
    with pytest.raises(ValueError,match="duplicate"):
        evaluate_chirp(wav,chirp([{"t_ms":20,"f_khz":80},
                                  {"t_ms":20,"f_khz":60}]),fake_reassignment)


def test_run_reads_only_calibration(tmp_path,monkeypatch):
    import laboratory.acoustic_audit as audit
    root=tmp_path/"dataset"
    manifest=[]
    for split in ("calibration","test"):
        wav=root/split/"wav"/"night"/f"{split}.wav"
        create_wav(wav)
        ann=root/split/"annotations"/"night"/"night.json"
        ann.parent.mkdir(parents=True,exist_ok=True)
        ann.write_text(json.dumps({"files":{wav.name:{"chirps":[chirp()]}}}))
        manifest.append({"partition":split,"session":split,
                         "dataset_wav":wav.relative_to(root).as_posix(),
                         "dataset_json":ann.relative_to(root).as_posix()})
    with (root/"manifest.csv").open("w",newline="") as stream:
        writer=csv.DictWriter(stream,fieldnames=manifest[0].keys())
        writer.writeheader()
        writer.writerows(manifest)
    monkeypatch.setattr(audit,"load_reassignment",lambda _:fake_reassignment)
    summary=run(root,tmp_path/"results")
    assert summary["chirps_processed"]==1
    assert summary["partition"]=="calibration"
    assert summary["errors"]==0
    with (tmp_path/"results"/"acoustic_chirps.csv").open(encoding="utf-8-sig") as file:
        records=list(csv.DictReader(file))
    assert len(records)==1 and records[0]["partition"]=="calibration"
