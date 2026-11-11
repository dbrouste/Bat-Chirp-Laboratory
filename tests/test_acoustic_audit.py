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


def fake_display(reader, frames, sr, **kwargs):
    from types import SimpleNamespace
    assert kwargs["representation"] == "mixed"
    assert kwargs["reassignment_percent"] == 35
    assert kwargs["fft_size"] == 1024
    assert kwargs["overlap_fraction"] == 0.75
    assert kwargs["spectral_alpha"] == 0.0
    assert reader(0, 10).shape == (10,)
    freq = np.fft.rfftfreq(kwargs["fft_size"], 1/sr)
    times = kwargs["start_time"] + np.linspace(.002, frames/sr-.002, 150)
    # Synthetic ridge with high amplitude at ~70 kHz, constant over time:
    # suitable for verifying sampling and axes (not a physical SNR).
    db = np.zeros((len(freq), len(times)), dtype=np.float32)
    db[np.argmin(abs(freq-70000)), :] = 40.0
    return SimpleNamespace(frequencies_hz=freq, times_s=times, db=db)


def test_evaluate_chirp_returns_finite_metrics(tmp_path):
    wav=tmp_path/"test.wav"
    create_wav(wav)
    row=evaluate_chirp(wav,chirp(),fake_display)
    assert row["samples_evaluated"]==20
    assert np.isfinite(row["snr_median_db"])
    assert 0<=row["support_fraction_3khz"]<=1


def test_invalid_points_rejected(tmp_path):
    wav=tmp_path/"test.wav"
    create_wav(wav)
    with pytest.raises(ValueError,match="duplicate"):
        evaluate_chirp(wav,chirp([{"t_ms":20,"f_khz":80},
                                  {"t_ms":20,"f_khz":60}]),fake_display)


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
    monkeypatch.setattr(audit,"load_display_pipeline",lambda _:fake_display)
    summary=run(root,tmp_path/"results")
    assert summary["chirps_processed"]==1
    assert summary["partition"]=="calibration"
    assert summary["reassignment_percent"]==35
    assert summary["stft_percent"]==65
    assert summary["representation"]=="Bat-Acoustic-Analyser mixed display"
    assert summary["errors"]==0
    with (tmp_path/"results"/"acoustic_chirps.csv").open(encoding="utf-8-sig") as file:
        records=list(csv.DictReader(file))
    assert len(records)==1 and records[0]["partition"]=="calibration"


def test_rejects_invalid_blend_settings(tmp_path):
    wav=tmp_path/"test.wav"
    create_wav(wav)
    with pytest.raises(ValueError,match="reassignment_percent"):
        evaluate_chirp(wav,chirp(),fake_display,reassignment_percent=110)


def test_real_gui_pipeline_is_importable_when_checkout_available(tmp_path):
    from pathlib import Path
    from laboratory.acoustic_audit import load_display_pipeline
    root=Path(__file__).resolve().parents[2]/"Bat-Acoustic-Analyser"
    if not (root/"bat_analyser"/"spectrogram_pipeline.py").is_file():
        pytest.skip("Real Bat-Acoustic-Analyser checkout not installed alongside laboratory")
    wav=tmp_path/"test.wav"
    create_wav(wav)
    pipeline=load_display_pipeline(root)
    row=evaluate_chirp(wav,chirp(),pipeline)
    assert row["samples_evaluated"]==20
    assert 0<=row["support_fraction_3khz"]<=1
