"""Tests for the recursive WAV + directory-level JSON corpus builder."""
import csv
import json
from pathlib import Path

import pytest

from laboratory.dataset_builder import build_dataset, partition_by_session


def record(manual=True, automatic=False):
    chirps = []
    if manual:
        chirps.append({"chirp_id": 1, "source": "manual", "status": "validated",
                       "points": [{"t_ms": 1, "f_khz": 100},
                                  {"t_ms": 2, "f_khz": 70}]})
    if automatic:
        chirps.append({"chirp_id": 2, "source": "automatic", "status": "draft"})
    return {"chirps": chirps}


def create_session(root, name, filename="same.wav", audio=None):
    directory = root / name
    directory.mkdir(parents=True)
    wav = directory / filename
    wav.write_bytes(audio if audio is not None else name.encode())
    annotation = directory / f"{directory.name}.json"
    annotation.write_text(json.dumps({"schema_version": 1, "units": {"time": "ms", "frequency": "kHz"}, "files": {filename: record()}}), encoding="utf-8")
    return wav, annotation


def rows(path):
    with path.open(encoding="utf-8-sig", newline="") as stream:
        return list(csv.DictReader(stream))


def test_build_copies_original_json_and_wavs_with_same_name(tmp_path):
    source = tmp_path / "source"
    source.mkdir()
    original = [create_session(source, f"session_{x}") for x in range(6)]
    output = tmp_path / "dataset"
    summary = build_dataset(source, output)
    inventory = rows(output / "manifest.csv")
    assert len(inventory) == 6
    assert summary["split"]["calibration"]["wav"] > 0
    assert summary["split"]["test"]["wav"] > 0
    for entry in inventory:
        assert (output / entry["dataset_wav"]).read_bytes() == Path(entry["source_wav"]).read_bytes()
        assert (output / entry["dataset_json"]).read_bytes() == Path(entry["source_json"]).read_bytes()
    assert len({entry["dataset_wav"] for entry in inventory}) == 6
    assert all(wav.is_file() and js.is_file() for wav, js in original)
    assert build_dataset(source, tmp_path / "other", dry_run=True)["split"] == summary["split"]


def test_whole_json_is_kept_together_and_automatic_is_not_a_reference(tmp_path):
    source = tmp_path / "source"
    wav, annotation = create_session(source, "night")
    extra = wav.parent / "extra.wav"
    extra.write_bytes(b"different")
    original = {"schema_version": 1, "units": {"time": "ms", "frequency": "kHz"}, "files": {
        "same.wav": record(manual=True, automatic=True),
        "extra.wav": record(manual=False, automatic=True)}}
    annotation.write_text(json.dumps(original, ensure_ascii=False, indent=4), encoding="utf-8")
    destination = tmp_path / "dataset"
    info = build_dataset(source, destination)
    assert info["manual_chirps"] == 1
    assert info["split"]["calibration"]["wav"] == 2
    assert info["split"]["test"]["wav"] == 0
    assert (destination / "calibration/annotations/night/night.json").read_bytes() == annotation.read_bytes()
    assert (destination / "calibration/wav/night/extra.wav").read_bytes() == b"different"
    assert "only one independent group" in (destination / "warnings.csv").read_text()


def test_missing_wav_skips_entire_json_and_does_not_create_broken_dataset(tmp_path):
    root = tmp_path / "src"
    root.mkdir()
    create_session(root, "good")
    other, annotations = create_session(root, "broken")
    other.unlink()
    result = build_dataset(root, tmp_path / "out")
    assert result["json_accepted"] == 1
    assert result["warnings"] >= 1
    assert len(rows(tmp_path / "out/manifest.csv")) == 1
    assert "missing WAV" in (tmp_path / "out/warnings.csv").read_text()


def test_duplicate_audio_never_leaks_across_partitions(tmp_path):
    source = tmp_path / "src"
    source.mkdir()
    create_session(source, "a", audio=b"identical")
    create_session(source, "b", audio=b"identical")
    create_session(source, "c", audio=b"different")
    info = build_dataset(source, tmp_path / "out", seed=13)
    inventory = rows(tmp_path / "out/manifest.csv")
    partition = {row["session"]: row["partition"] for row in inventory}
    assert partition["a"] == partition["b"]
    assert info["duplicate_audio_files"] == 1


def test_invalid_metadata_and_only_automatic_are_skipped(tmp_path):
    root = tmp_path / "src"
    root.mkdir()
    create_session(root, "good")
    (root / "metadata.json").write_text('{"settings": {}}')
    folder = root / "auto"
    folder.mkdir()
    (folder / "auto.wav").write_bytes(b"sound")
    (folder / "auto.json").write_text(json.dumps({"schema_version": 1, "units": {"time": "ms", "frequency": "kHz"}, "files": {"auto.wav": record(False, True)}}))
    (root / "invalid.json").write_text("{invalid", encoding="utf-8")
    output = tmp_path / "out"
    result = build_dataset(root, output)
    assert result["json_accepted"] == 1
    assert result["json_scanned"] == 4
    assert "invalid JSON" in (output / "warnings.csv").read_text()
    assert "no validated manual" in (output / "warnings.csv").read_text()


def test_output_protections_and_no_changes_in_dry_run(tmp_path):
    source = tmp_path / "src"
    source.mkdir()
    create_session(source, "night")
    with pytest.raises(ValueError, match="outside source"):
        build_dataset(source, source / "out")
    out = tmp_path / "out"
    out.mkdir()
    (out / "sentinel.txt").write_text("DO NOT OVERWRITE")
    with pytest.raises(ValueError, match="not empty"):
        build_dataset(source, out)
    build_dataset(source, out, dry_run=True)
    assert list(p.name for p in out.iterdir()) == ["sentinel.txt"]
    with pytest.raises(ValueError, match="test_ratio"):
        build_dataset(source, tmp_path / "other", test_ratio=1.0)


def test_nested_paths_and_nonexistent_root(tmp_path):
    root = tmp_path / "root"
    root.mkdir()
    folder = root / "site" / "night"
    folder.mkdir(parents=True)
    (folder / "call.WAV").write_bytes(b"audio")
    (folder / "index.json").write_text(json.dumps({
        "created_by": "Bat-Acoustic-Analyser",
        "schema_version": 1,
        "units": {"time": "ms", "frequency": "kHz"},
        "files": {"call.wav": record()}}))
    summary = build_dataset(root, tmp_path / "out")
    assert summary["wav_accepted"] == 1
    assert (tmp_path / "out/calibration/wav/site/night/call.WAV").exists()
    with pytest.raises(ValueError, match="not found"):
        build_dataset(tmp_path / "nonexistent", tmp_path / "other")

def test_rejects_foreign_creator_even_with_matching_shape(tmp_path):
    root = tmp_path / "src"
    root.mkdir()
    create_session(root, "good")
    wav, annotation = create_session(root, "foreign")
    data = json.loads(annotation.read_text(encoding="utf-8"))
    data["created_by"] = "Other software"
    annotation.write_text(json.dumps(data))
    summary = build_dataset(root, tmp_path / "out")
    assert summary["json_accepted"] == 1
    assert "foreign created_by" in (tmp_path / "out/warnings.csv").read_text()


def test_legacy_needs_directory_name_schema_units_and_valid_points(tmp_path):
    root = tmp_path / "src"
    root.mkdir()
    create_session(root, "good")
    _, wrong_name = create_session(root, "wrong_name")
    wrong_name.rename(wrong_name.with_name("unrelated.json"))
    _, bad_units = create_session(root, "bad_units")
    doc = json.loads(bad_units.read_text())
    doc["units"]["frequency"] = "Hz"
    bad_units.write_text(json.dumps(doc))
    _, bad_points = create_session(root, "bad_points")
    doc = json.loads(bad_points.read_text())
    doc["files"]["same.wav"]["chirps"][0]["points"] = [{"t_ms": 1, "f_khz": 60}]
    bad_points.write_text(json.dumps(doc))
    summary = build_dataset(root, tmp_path / "out")
    assert summary["json_accepted"] == 1
    assert summary["json_legacy_accepted"] == 1
    warning_text = (tmp_path / "out/warnings.csv").read_text()
    assert "filename differs" in warning_text
    assert "missing/invalid units" in warning_text
    assert "fewer than 2 points" in warning_text


def test_tagged_json_accepted_with_nonstandard_filename(tmp_path):
    root = tmp_path / "src"
    root.mkdir()
    _, annotation = create_session(root, "night")
    doc = json.loads(annotation.read_text())
    doc["created_by"] = "Bat-Acoustic-Analyser"
    renamed = annotation.with_name("annotations-export.json")
    renamed.write_text(json.dumps(doc))
    annotation.unlink()
    summary = build_dataset(root, tmp_path / "out")
    assert summary["json_tagged_accepted"] == 1
    assert summary["json_legacy_accepted"] == 0
    assert (tmp_path / "out/calibration/annotations/night/annotations-export.json").is_file()
