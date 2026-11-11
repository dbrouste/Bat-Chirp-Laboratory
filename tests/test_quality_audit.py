"""Unit tests for the read-only annotation quality audit."""
import csv
import json
from pathlib import Path
import wave

from laboratory.quality_audit import inspect_chirp, audit_dataset


def test_flags_invalid_geometry():
    good={"chirp_id":1,"points":[{"t_ms":1,"f_khz":80},{"t_ms":3,"f_khz":60}]}
    assert inspect_chirp(good,0.1,384000)["flags"] == ""
    bad={"chirp_id":2,"points":[{"t_ms":1,"f_khz":80},{"t_ms":1,"f_khz":60}]}
    assert "nonincreasing_time" in inspect_chirp(bad,0.1,384000)["flags"]
    high={"chirp_id":3,"points":[{"t_ms":10,"f_khz":210},{"t_ms":12,"f_khz":80}]}
    assert "above_nyquist" in inspect_chirp(high,0.1,384000)["flags"]


def test_reports_calibration_only_and_never_mutates_data(tmp_path):
    root=tmp_path/"dataset"
    manifest=[]
    for split in ("calibration","test"):
        wav=root/split/"wav"/"night"/f"{split}.wav"
        wav.parent.mkdir(parents=True)
        with wave.open(str(wav),"wb") as handle:
            handle.setnchannels(1)
            handle.setsampwidth(2)
            handle.setframerate(384000)
            handle.writeframes(b"\0\0"*38400)
        ann=root/split/"annotations"/"night"/"night.json"
        ann.parent.mkdir(parents=True)
        ann.write_text(json.dumps({"files":{wav.name:{"chirps":[
            {"source":"manual","status":"validated","chirp_id":1,"points":[
                {"t_ms":1,"f_khz":80},{"t_ms":3,"f_khz":60}]},
            {"source":"automatic","status":"draft","points":[]}
        ]}}}))
        manifest.append({"partition":split,"session":"night",
                         "dataset_wav":wav.relative_to(root).as_posix(),
                         "dataset_json":ann.relative_to(root).as_posix()})
    with (root/"manifest.csv").open("w",encoding="utf-8",newline="") as stream:
        writer=csv.DictWriter(stream,fieldnames=manifest[0].keys())
        writer.writeheader()
        writer.writerows(manifest)
    original=(root/"calibration"/"annotations"/"night"/"night.json").read_bytes()
    summary=audit_dataset(root,tmp_path/"quality")
    assert summary["manual_chirps_inspected"] == 1
    assert summary["flagged_chirps"] == 0
    assert (root/"calibration"/"annotations"/"night"/"night.json").read_bytes() == original
    assert audit_dataset(root,tmp_path/"all",include_test=True)["manual_chirps_inspected"] == 2
