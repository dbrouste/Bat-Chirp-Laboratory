"""Tests of the frozen-dataset annotation refresh workflow."""
import csv
import json
from pathlib import Path

import pytest

from laboratory.dataset_builder import build_dataset
from laboratory.dataset_refresh import refresh_annotations


def make_session(root: Path, session: str, name: str):
    folder = root/session
    folder.mkdir(parents=True)
    (folder/name).write_bytes(session.encode())
    annotation = folder/f"{session}.json"
    annotation.write_text(json.dumps({
        "schema_version": 1, "units": {"time":"ms","frequency":"kHz"},
        "files": {name: {"chirps": [
            {"source":"manual","status":"validated","chirp_id":1,"points":[
                {"t_ms":1,"f_khz":85},{"t_ms":3,"f_khz":65}]}]}}}))
    return annotation


def test_refresh_keeps_splits_updates_counts_and_backs_up(tmp_path):
    source = tmp_path/"src"
    source.mkdir()
    one = make_session(source,"session1","first.wav")
    make_session(source,"session2","second.wav")
    dataset = tmp_path/"dataset"
    build_dataset(source,dataset,seed=3)
    with (dataset/"manifest.csv").open(encoding="utf-8-sig") as file:
        original = list(csv.DictReader(file))
    # Edit whichever source ended up in calibration.
    selected = next(r for r in original if r["partition"] == "calibration")
    annotation = Path(selected["source_json"])
    doc = json.loads(annotation.read_text())
    key = Path(selected["source_wav"]).name
    doc["files"][key]["chirps"].append({
        "source":"manual","status":"validated","chirp_id":2,
        "points":[{"t_ms":5,"f_khz":90},{"t_ms":7,"f_khz":60}]})
    annotation.write_text(json.dumps(doc))
    old_dataset_json = dataset/selected["dataset_json"]
    old_bytes = old_dataset_json.read_bytes()
    pre = refresh_annotations(dataset,key,dry_run=True)
    assert pre["changed"]
    assert old_dataset_json.read_bytes() == old_bytes
    updated = refresh_annotations(dataset,key)
    assert updated["changed"]
    with (dataset/"manifest.csv").open(encoding="utf-8-sig") as file:
        after = list(csv.DictReader(file))
    assert [(r["session"],r["partition"]) for r in after] == [
        (r["session"],r["partition"]) for r in original]
    new = next(r for r in after if r["source_wav"] == selected["source_wav"])
    assert new["validated_manual"] == "2"
    assert old_dataset_json.read_bytes() == annotation.read_bytes()
    assert list((dataset/"previous_metadata").glob("*.json"))
    assert refresh_annotations(dataset,key)["changed"] is False


def test_refuses_test_edit_or_changed_wav(tmp_path):
    source=tmp_path/"src"
    source.mkdir()
    make_session(source,"first","a.wav")
    make_session(source,"second","b.wav")
    dataset=tmp_path/"dataset"
    build_dataset(source,dataset)
    with (dataset/"manifest.csv").open(encoding="utf-8-sig") as file:
        records=list(csv.DictReader(file))
    test=next(r for r in records if r["partition"]=="test")
    with pytest.raises(ValueError,match="test partition"):
        refresh_annotations(dataset,Path(test["source_wav"]).name)
    calibration=next(r for r in records if r["partition"]=="calibration")
    Path(calibration["source_wav"]).write_bytes(b"changed")
    with pytest.raises(ValueError,match="WAV changed"):
        refresh_annotations(dataset,Path(calibration["source_wav"]).name)
