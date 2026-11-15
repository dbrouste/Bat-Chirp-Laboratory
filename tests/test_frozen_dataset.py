"""Frozen calibration dataset and initial benchmark compatibility tests."""
import csv
import json
from pathlib import Path

import pytest

from laboratory.dataset_builder import build_dataset
from laboratory.frozen_dataset import collect_frozen_dataset
from laboratory import runner


def _make_dataset(tmp_path, *, include_unannotated=False):
    source = tmp_path / "originals"
    for night, name in (("night_a", "same.wav"), ("night_b", "same.wav")):
        folder = source / night
        folder.mkdir(parents=True)
        (folder / name).write_bytes(f"audio-{night}".encode())
        files = {name: {"chirps": [{
            "chirp_id": 1, "source": "manual", "status": "validated",
            "points": [{"t_ms": 1, "f_khz": 80},
                       {"t_ms": 3, "f_khz": 60}],
        }]}}
        if include_unannotated:
            # Sorts ahead of the human-reviewed WAV in manifest.csv.
            (folder / "000_empty.wav").write_bytes(b"unannotated")
            files["000_empty.wav"] = {"chirps": []}
        (folder / f"{night}.json").write_text(json.dumps({
            "schema_version": 1,
            "units": {"time": "ms", "frequency": "kHz"},
            "files": files,
        }), encoding="utf-8")
    dataset = tmp_path / "dataset-v1"
    build_dataset(source, dataset, seed=3)
    with (dataset / "manifest.csv").open(encoding="utf-8-sig", newline="") as stream:
        rows = list(csv.DictReader(stream))
    return dataset, rows


def test_collects_only_frozen_calibration_not_test(tmp_path):
    dataset, rows = _make_dataset(tmp_path)
    records, provenance = collect_frozen_dataset(dataset)
    calibration = [r for r in rows if r["partition"] == "calibration"]
    test = [r for r in rows if r["partition"] == "test"]
    assert calibration and test
    assert set(records) == {r["dataset_wav"] for r in calibration}
    assert provenance["partition"] == "calibration"
    assert provenance["hashes_verified"] is True
    assert len(provenance["manifest_sha256"]) == 64
    assert all(value["chirps"][0]["status"] == "validated"
               for value in records.values())
    with pytest.raises(ValueError, match="Only calibration"):
        collect_frozen_dataset(dataset, partition="test")


def test_frozen_manifest_rejects_modified_wav_and_json(tmp_path):
    dataset, rows = _make_dataset(tmp_path)
    row = next(r for r in rows if r["partition"] == "calibration")
    wav = dataset / row["dataset_wav"]
    wav.write_bytes(b"tampered audio")
    with pytest.raises(ValueError, match="WAV fingerprint mismatch"):
        collect_frozen_dataset(dataset)
    # Independent fixture for the JSON fingerprint.
    fresh, lines = _make_dataset(tmp_path / "second")
    selected = next(r for r in lines if r["partition"] == "calibration")
    annotation = fresh / selected["dataset_json"]
    annotation.write_text(annotation.read_text() + " ", encoding="utf-8")
    with pytest.raises(ValueError, match="JSON fingerprint mismatch"):
        collect_frozen_dataset(fresh)


def test_frozen_manifest_cannot_escape_directory(tmp_path):
    dataset, rows = _make_dataset(tmp_path)
    manifest = dataset / "manifest.csv"
    for row in rows:
        if row["partition"] == "calibration":
            row["dataset_wav"] = "calibration/wav/../../../../outside.wav"
            break
    with manifest.open("w", encoding="utf-8-sig", newline="") as stream:
        writer = csv.DictWriter(stream, fieldnames=list(rows[0]))
        writer.writeheader()
        writer.writerows(rows)
    with pytest.raises(ValueError, match="escapes its root"):
        collect_frozen_dataset(dataset)


def test_baseline_runner_uses_manifest_and_is_read_only(tmp_path, monkeypatch):
    dataset, rows = _make_dataset(tmp_path)
    originals = {
        p: p.read_bytes() for p in dataset.rglob("*") if p.is_file()
    }
    external = tmp_path / "external"
    (external / "bat_analysis").mkdir(parents=True)
    (external / "benchmark").mkdir()
    (external / "bat_analysis" / "detection.py").write_text("stub\n")
    (external / "benchmark" / "reassignment_detector_seeds.py").write_text(
        "def evaluate(*args, geometry_mode=None, **kwargs): pass\n")
    invoked = []

    def fake_worker(name, record, wav_root, options, processing_root):
        invoked.append(name)
        assert Path(wav_root) == dataset.resolve()
        return {"file": name, "elapsed_s": .01,
                "per_file": [{"file": name, "annotated": 1, "matched": 0,
                              "candidates": 0, "tracking_succeeded": 0}],
                "details": [{"file": name, "chirp_id": 1,
                             "detection_importance_weight": 1,
                             "snr_max_db": 30, "detector_matched": False,
                             "tracking_success": False}]}

    monkeypatch.setattr(runner, "_worker", fake_worker)
    monkeypatch.setattr(runner, "code_fingerprint", lambda _root: "algorithm-v1")
    report = runner.run(dataset=dataset, out=tmp_path / "baseline",
                        workers=1, processing_root=external)
    assert report["metadata"]["status"] == "complete"
    assert report["metadata"]["frozen_dataset"]["partition"] == "calibration"
    assert report["totals"]["files_evaluated"] == len(invoked)
    assert len(invoked) == sum(r["partition"] == "calibration" for r in rows)
    assert {p: p.read_bytes() for p in originals} == originals
    assert (tmp_path / "baseline" / "report.json").is_file()
    with pytest.raises(ValueError, match="outside"):
        runner.run(dataset=dataset, out=dataset / "results",
                   workers=1, processing_root=external)


def test_small_frozen_baseline_skips_wavs_without_manual_reference(tmp_path, monkeypatch):
    dataset, rows = _make_dataset(tmp_path, include_unannotated=True)
    calibration = [r for r in rows if r["partition"] == "calibration"]
    assert any(Path(r["dataset_wav"]).name == "000_empty.wav" for r in calibration)

    external = tmp_path / "external"
    (external / "bat_analysis").mkdir(parents=True)
    (external / "benchmark").mkdir()
    (external / "bat_analysis" / "detection.py").write_text("# fake detector\\n")
    (external / "benchmark" / "reassignment_detector_seeds.py").write_text(
        "def evaluate(*args, geometry_mode=None, **kwargs): pass\\n")
    invoked = []

    def fake_worker(name, record, wav_root, options, processing_root):
        assert any(c.get("source") == "manual" and c.get("status") == "validated"
                   for c in record["chirps"])
        invoked.append(name)
        return {"file": name, "elapsed_s": .01,
                "per_file": [{"file": name, "annotated": 1, "matched": 0,
                              "candidates": 0, "tracking_succeeded": 0}],
                "details": [{"file": name, "chirp_id": 1,
                             "detection_importance_weight": 1,
                             "snr_max_db": 30, "detector_matched": False,
                             "tracking_success": False}]}
    monkeypatch.setattr(runner, "_worker", fake_worker)
    monkeypatch.setattr(runner, "code_fingerprint", lambda _root: "baseline-v1")
    report = runner.run(dataset=dataset, out=tmp_path / "baseline",
                        max_files=1, workers=1, processing_root=external)
    assert len(invoked) == 1
    assert Path(invoked[0]).name == "same.wav"
    provenance = report["metadata"]["frozen_dataset"]
    # The builder partitions complete sessions, so both synthetic WAVs
    # without manual references can land in calibration. Derive the expected
    # count from the frozen manifest instead of assuming one such WAV.
    expected_without_reference = sum(
        Path(row["dataset_wav"]).name == "000_empty.wav"
        for row in calibration
    )
    expected_with_reference = len(calibration) - expected_without_reference
    assert provenance["wav_without_reference_skipped"] == expected_without_reference
    assert provenance["wav_with_reference"] == expected_with_reference
    assert report["totals"]["annotated"] == 1
