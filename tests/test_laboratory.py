"""Laboratory guarantees: read-only truth, cache invalidation, aggregation."""

import json
from pathlib import Path
import sqlite3

import pytest

from laboratory.dataset import collect_annotations, fingerprint_inputs
from laboratory import runner


PROCESSING_ROOT = Path(__file__).resolve().parents[2] / "bat_full_spectrum_processing"


def _manual():
    return {"chirps": [{"chirp_id": 1, "source": "manual",
                        "status": "validated",
                        "points": [{"t_ms": 1, "f_khz": 80},
                                   {"t_ms": 3, "f_khz": 60}]}]}


def test_collects_recursive_annotations_and_keeps_duplicate_wav_names_distinct(tmp_path):
    wav_root = tmp_path / "wavs"
    annotations = wav_root / "annotations"
    for site in ("site_A", "site_B"):
        directory = wav_root / site
        directory.mkdir(parents=True)
        (directory / "same.wav").write_bytes(b"wav")
        folder = annotations / site
        folder.mkdir(parents=True)
        (folder / "chirps.json").write_text(json.dumps({
            "files": {"same.wav": _manual()}}), encoding="utf-8")
    data = collect_annotations(annotations, wav_root)
    assert list(data) == ["site_A/same.wav", "site_B/same.wav"]
    assert data["site_A/same.wav"]["chirps"][0]["source"] == "manual"


def test_rejects_ambiguous_records_and_traversal(tmp_path):
    wav_root = tmp_path / "wavs"
    wav_root.mkdir()
    (wav_root / "a.wav").write_bytes(b"audio")
    docs = tmp_path / "docs"
    docs.mkdir()
    for file in ("one.json", "two.json"):
        (docs / file).write_text(json.dumps({"files": {"a.wav": _manual()}}),
                                       encoding="utf-8")
    with pytest.raises(ValueError, match="Duplicate WAV"):
        collect_annotations(docs, wav_root)
    (docs / "two.json").unlink()
    (docs / "one.json").write_text(json.dumps({
        "files": {"../outside.wav": _manual()}}), encoding="utf-8")
    with pytest.raises(ValueError, match="outside WAV root"):
        collect_annotations(docs / "one.json", wav_root)


def test_audio_and_annotations_invalidate_cache_key(tmp_path):
    (tmp_path / "a.wav").write_bytes(b"first")
    records = {"a.wav": _manual()}
    first = fingerprint_inputs(records, tmp_path)
    (tmp_path / "a.wav").write_bytes(b"second")
    assert fingerprint_inputs(records, tmp_path)["a.wav"]["wav_sha256"] != (
        first["a.wav"]["wav_sha256"])
    records["a.wav"]["chirps"][0]["points"][1]["f_khz"] = 61
    assert fingerprint_inputs(records, tmp_path)["a.wav"]["annotation_sha256"] != (
        first["a.wav"]["annotation_sha256"])


def test_runner_caches_results_without_changing_manual_json(tmp_path, monkeypatch):
    wav_root = tmp_path / "wavs"
    wav_root.mkdir()
    (wav_root / "a.wav").write_bytes(b"synthetic")
    annotation_file = tmp_path / "manual.json"
    annotation_file.write_text(json.dumps({"files": {"a.wav": _manual()}}),
                               encoding="utf-8")
    untouched = annotation_file.read_bytes()
    external = tmp_path / "external"
    (external / "bat_analysis").mkdir(parents=True)
    (external / "benchmark").mkdir()
    (external / "bat_analysis" / "detection.py").write_text("# placeholder\n")
    (external / "benchmark" / "reassignment_detector_seeds.py").write_text(
        "# placeholder\n")
    invoked = []

    def fake_worker(name, record, wav_dir, options, processing_root,
                    scoring="snr", geometry_config=None):
        invoked.append(name)
        return {"file": name, "elapsed_s": 1.0, "details": [
            {"file": name, "chirp_id": 1, "snr_max_db": 40.,
             "detection_importance_weight": 1., "detector_matched": True,
             "tracking_success": True, "within_snr_tolerance_fraction": 0.8,
             "mean_full_interval_error_khz": 2.0,
             "mean_normalized_error": 0.67, "coverage_fraction": 1.0,
             "start_error_ms": 0.0, "end_error_ms": 0.0}],
            "per_file": [{"file": name, "annotated": 1, "candidates": 1,
                          "matched": 1, "tracking_succeeded": 1}]}

    monkeypatch.setattr(runner, "_worker", fake_worker)
    monkeypatch.setattr(runner, "code_fingerprint", lambda _root: "same-code")
    output = tmp_path / "out"
    one = runner.run(annotations=annotation_file, wav_dir=wav_root,
                     out=output, workers=1, processing_root=external)
    two = runner.run(annotations=annotation_file, wav_dir=wav_root,
                     out=output, workers=1, processing_root=external)
    assert invoked == ["a.wav"]
    assert one["totals"]["weighted_detector_recall"] == pytest.approx(1)
    assert two["metadata"]["files_from_cache"] == 1
    assert (output / "report.html").is_file()
    assert annotation_file.read_bytes() == untouched
    with sqlite3.connect(output / "experiments.sqlite") as connection:
        assert connection.execute("SELECT COUNT(*) FROM experiments").fetchone()[0] == 2


def test_summarizing_independent_wavs_is_weighted_by_chirps():
    from laboratory.aggregation import summarize_results

    per_file = [{"file": "a.wav", "annotated": 1},
                {"file": "b.wav", "annotated": 2}]
    details = [
        {"detection_importance_weight": 1, "detector_matched": True,
         "tracking_success": True, "snr_max_db": 40,
         "mean_full_interval_error_khz": 1,
         "mean_normalized_error": .5,
         "within_snr_tolerance_fraction": 1,
         "coverage_fraction": 1, "start_error_ms": 0, "end_error_ms": 0},
        {"detection_importance_weight": .2, "detector_matched": False,
         "tracking_success": False, "snr_max_db": 10},
        {"detection_importance_weight": .2, "detector_matched": False,
         "tracking_success": False, "snr_max_db": 10},
    ]
    totals = summarize_results(per_file, details)
    assert totals["annotated"] == 3
    assert totals["weighted_detector_recall"] == pytest.approx(1 / 1.4)
    assert totals["full_pipeline_within_tolerance_fraction"] == pytest.approx(1 / 3)


@pytest.mark.skipif(
    not (PROCESSING_ROOT / "benchmark" / "reassignment_detector_seeds.py").is_file(),
    reason="External processing checkout required for integration test")
def test_spawn_workers_end_to_end_on_silent_wavs(tmp_path):
    """Exercise real subprocess imports, Windows-style spawn and report merge."""
    import numpy as np
    import soundfile as sf

    wav_root = tmp_path / "wav"
    wav_root.mkdir()
    names = ["a.wav", "b.wav"]
    for name in names:
        sf.write(wav_root / name, np.zeros(19200), 384000)
    annotations = tmp_path / "manual.json"
    annotations.write_text(json.dumps({"files": {
        name: {"chirps": []} for name in names}}), encoding="utf-8")
    report = runner.run(annotations=annotations, wav_dir=wav_root,
                        out=tmp_path / "results", workers=2,
                        processing_root=PROCESSING_ROOT)
    assert report["metadata"]["status"] == "complete"
    assert report["totals"]["files_evaluated"] == 2
    assert report["totals"]["annotated"] == 0
    assert report["metadata"]["files_from_cache"] == 0


@pytest.mark.skipif(
    not (PROCESSING_ROOT / "benchmark" / "reassignment_detector_seeds.py").is_file(),
    reason="External processing checkout required for integration test")
def test_geometry_spawn_workers_end_to_end_on_silent_wavs(tmp_path):
    import numpy as np
    import soundfile as sf

    wav_root = tmp_path / "wav"
    wav_root.mkdir()
    for name in ("a.wav", "b.wav"):
        sf.write(wav_root / name, np.zeros(19200), 384000)
    annotations = tmp_path / "manual.json"
    annotations.write_text(json.dumps({"files": {
        name: {"chirps": []} for name in ("a.wav", "b.wav")}}),
        encoding="utf-8")
    report = runner.run(annotations=annotations, wav_dir=wav_root,
                        out=tmp_path / "results", workers=2,
                        processing_root=PROCESSING_ROOT, scoring="geometry")
    assert report["metadata"]["status"] == "complete"
    assert report["totals"]["files_evaluated"] == 2
    assert report["totals"]["annotated"] == 0
    assert report["totals"]["false_positive_count"] is None
    assert "géométrique" in (tmp_path / "results" / "report.html").read_text(
        encoding="utf-8")
