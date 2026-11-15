"""Explicit no_chirp WAVs form a negative cohort; missing labels do not."""
import csv
import json
from pathlib import Path

import pytest

from laboratory import runner
from laboratory.dataset_builder import build_dataset


def _make(tmp_path, *, conflict=False):
    source = tmp_path / "source"
    for n in ("a", "b", "c"):
        folder = source / n
        folder.mkdir(parents=True)
        positive = "call.wav"
        negative = "noise.wav"
        unknown = "not-reviewed.wav"
        for filename in (positive, negative, unknown):
            (folder / filename).write_bytes((n + filename).encode())
        manual = {
            "chirp_id": 1, "source": "manual", "status": "validated",
            "points": [{"t_ms": 1, "f_khz": 80},
                       {"t_ms": 3, "f_khz": 40}],
        }
        files = {
            positive: {"chirps": [manual]},
            negative: {"review_status": "no_chirp",
                       "chirps": (
                           [{"chirp_id": 2, "source": "manual",
                             "status": "draft"}] if conflict else
                           [{"chirp_id": 2, "source": "automatic",
                             "status": "draft"}])},
            unknown: {"chirps": []},
        }
        (folder / f"{n}.json").write_text(json.dumps({
            "schema_version": 1,
            "units": {"time": "ms", "frequency": "kHz"},
            "files": files,
        }), encoding="utf-8")
    frozen = tmp_path / "dataset"
    build_dataset(source, frozen, seed=9)
    with (frozen / "manifest.csv").open(
        encoding="utf-8-sig", newline=""
    ) as stream:
        rows = list(csv.DictReader(stream))
    return frozen, rows


def _run(tmp_path, monkeypatch, dataset, *, include, max_files=None):
    external = tmp_path / "processing"
    (external / "bat_analysis").mkdir(parents=True, exist_ok=True)
    (external / "benchmark").mkdir(exist_ok=True)
    (external / "bat_analysis" / "detection.py").write_text("# stub\n")
    (external / "benchmark" / "reassignment_detector_seeds.py").write_text(
        "def evaluate(*args, geometry_mode=None, **kwargs): pass\n")
    calls = []

    def fake_worker(name, record, root, options, processing):
        calls.append((name, record))
        is_negative = record.get("review_status") == "no_chirp"
        return {
            "file": name, "elapsed_s": .01,
            "per_file": [{
                "file": name,
                "annotated": 0 if is_negative else 1,
                "matched": 0 if is_negative else 1,
                "candidates": 2 if is_negative else 11,
                "tracking_succeeded": 0,
            }],
            "details": ([] if is_negative else [{
                "file": name, "chirp_id": 1,
                "detection_importance_weight": 1,
                "snr_max_db": 40, "detector_matched": True,
                "tracking_success": False,
            }]),
        }

    monkeypatch.setattr(runner, "_worker", fake_worker)
    monkeypatch.setattr(runner, "code_fingerprint", lambda _root: "algo-neg")
    report = runner.run(
        dataset=dataset, out=tmp_path / ("with" if include else "without"),
        include_reviewed_negatives=include,
        max_files=max_files, workers=1, processing_root=external)
    return report, calls


def test_only_explicit_reviewed_no_chirp_is_negative(tmp_path, monkeypatch):
    frozen, rows = _make(tmp_path)
    calibration = [r for r in rows if r["partition"] == "calibration"]
    pos = sum(Path(r["dataset_wav"]).name == "call.wav" for r in calibration)
    neg = sum(Path(r["dataset_wav"]).name == "noise.wav" for r in calibration)
    unknown = sum(Path(r["dataset_wav"]).name == "not-reviewed.wav"
                  for r in calibration)
    assert pos and neg and unknown
    original = {p: p.read_bytes() for p in frozen.rglob("*") if p.is_file()}

    report, calls = _run(tmp_path, monkeypatch, frozen, include=True)
    assert len(calls) == pos + neg
    assert all("test/" not in name for name, _ in calls)
    assert not any(Path(name).name == "not-reviewed.wav" for name, _ in calls)
    assert report["totals"]["annotated"] == pos
    assert report["totals"]["matched"] == pos
    neg_metrics = report["totals"]["reviewed_no_chirp"]
    assert neg_metrics["wav_count"] == neg
    assert neg_metrics["candidate_alerts"] == 2 * neg
    assert neg_metrics["wavs_with_candidate_alert"] == neg
    assert neg_metrics["wav_false_alarm_fraction"] == 1.0
    assert neg_metrics["metric_scope"] == (
        "detector candidates, not fully tracked chirps")
    assert report["metadata"]["positive_annotations_exhaustive"] is False
    assert report["metadata"]["include_reviewed_negatives"] is True
    assert len([r for r in report["per_file"]
                if r.get("reference_class") == "reviewed_no_chirp"]) == neg
    assert report["metadata"]["frozen_dataset"]["reviewed_no_chirp_available"] == neg
    assert report["metadata"]["frozen_dataset"]["wav_without_reference_skipped"] == unknown
    assert {p: p.read_bytes() for p in original} == original


def test_default_does_not_assume_missing_annotations_are_negative(tmp_path, monkeypatch):
    frozen, rows = _make(tmp_path)
    pos = sum(r["partition"] == "calibration"
              and Path(r["dataset_wav"]).name == "call.wav" for r in rows)
    report, calls = _run(tmp_path, monkeypatch, frozen, include=False)
    assert len(calls) == pos
    assert report["totals"]["reviewed_no_chirp"]["wav_count"] == 0
    assert report["metadata"]["frozen_dataset"]["reviewed_no_chirp_evaluated"] == 0


def test_conflicting_no_chirp_with_manual_curves_is_rejected(tmp_path, monkeypatch):
    frozen, _ = _make(tmp_path, conflict=True)
    with pytest.raises(ValueError, match="Conflicting no_chirp"):
        _run(tmp_path, monkeypatch, frozen, include=True)


def test_max_files_counts_positive_refs_first(tmp_path, monkeypatch):
    frozen, _ = _make(tmp_path)
    report, calls = _run(tmp_path, monkeypatch, frozen, include=True,
                         max_files=1)
    assert len(calls) == 1
    assert Path(calls[0][0]).name == "call.wav"
    assert report["totals"]["reviewed_no_chirp"]["wav_count"] == 0


def test_legacy_mode_cannot_mistake_missing_labels_for_negatives(tmp_path):
    with pytest.raises(ValueError, match="frozen"):
        runner.run(
            annotations=tmp_path / "annotations.json",
            wav_dir=tmp_path, out=tmp_path / "output",
            include_reviewed_negatives=True)
