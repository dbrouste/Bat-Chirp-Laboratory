"""Reproducibility checks using temporary source trees and synthetic bytes."""

import csv
import json
import os
from pathlib import Path
from types import SimpleNamespace

import pytest

from laboratory import fingerprints, local_campaign, runner


@pytest.fixture
def source_trees(tmp_path, monkeypatch):
    roots = {}
    for label, packages in (
        ("laboratory", ("laboratory", "chirp_geometry")),
        ("processing", ("bat_analysis", "benchmark")),
        ("analyser", ("bat_analyser",)),
    ):
        root = tmp_path / label
        for package in packages:
            folder = root / package
            folder.mkdir(parents=True)
            (folder / "__init__.py").write_text("value = 1\n", encoding="utf-8")
        (root / "requirements.txt").write_text("numpy\n", encoding="utf-8")
        roots[label] = root
    monkeypatch.setattr(runner, "ROOT", roots["laboratory"])
    monkeypatch.setattr(local_campaign, "ROOT", roots["laboratory"])
    monkeypatch.setattr(local_campaign, "DEFAULT_ANALYSER_ROOT", roots["analyser"])
    monkeypatch.setattr(fingerprints, "runtime_identity", lambda: {"packages": []})
    return roots


@pytest.fixture
def campaign_inputs(tmp_path, source_trees):
    dataset = tmp_path / "dataset"
    dataset.mkdir()
    (dataset / "audio.wav").write_bytes(b"audio")
    (dataset / "manual.json").write_text("{}", encoding="utf-8")
    with (dataset / "manifest.csv").open("w", newline="", encoding="utf-8") as stream:
        writer = csv.DictWriter(stream, fieldnames=("partition", "dataset_wav", "dataset_json"))
        writer.writeheader()
        writer.writerow(dict(partition="calibration", dataset_wav="audio.wav",
                             dataset_json="manual.json"))
        writer.writerow(dict(partition="test", dataset_wav="absent.wav",
                             dataset_json="absent.json"))
    return dataset


def campaign_key(dataset, **overrides):
    settings = dict(max_chirps=10, skip_chirps=0, fft_size=1024, stft_hop=32,
                    reassignment_hop=16, spectral_alpha=0.0, window=5)
    settings.update(overrides)
    return local_campaign.run_key(dataset, **settings)


@pytest.mark.parametrize("parameter,value", [
    ("max_chirps", 11), ("skip_chirps", 1), ("fft_size", 512),
    ("stft_hop", 16), ("reassignment_hop", 32),
    ("spectral_alpha", 1.0), ("window", 7),
])
def test_campaign_parameters_invalidate(campaign_inputs, parameter, value):
    baseline = campaign_key(campaign_inputs)
    assert campaign_key(campaign_inputs) == baseline
    assert campaign_key(campaign_inputs, **{parameter: value}) != baseline


@pytest.mark.parametrize("name", ["audio.wav", "manual.json"])
def test_campaign_hashes_content_even_when_size_and_mtime_match(campaign_inputs, name):
    baseline = campaign_key(campaign_inputs)
    path = campaign_inputs / name
    original = path.stat()
    path.write_bytes(b"x" * original.st_size)
    os.utime(path, ns=(original.st_atime_ns, original.st_mtime_ns))
    assert campaign_key(campaign_inputs) != baseline


@pytest.mark.parametrize("relative", [
    "chirp_geometry/__init__.py", "laboratory/aggregation.py",
    "laboratory/report.py", "laboratory/dataset.py", "pyproject.toml",
    "requirements.txt", "requirements-windows-py313.lock",
])
def test_shared_source_and_configuration_changes_invalidate_both_caches(
        source_trees, campaign_inputs, relative):
    path = source_trees["laboratory"] / relative
    path.write_text("original", encoding="utf-8")
    benchmark = runner.code_fingerprint(source_trees["processing"])
    campaign = campaign_key(campaign_inputs)
    path.write_text("modified", encoding="utf-8")
    assert runner.code_fingerprint(source_trees["processing"]) != benchmark
    assert campaign_key(campaign_inputs) != campaign


def test_external_rendering_and_default_analyser_are_fingerprinted(source_trees, campaign_inputs):
    baseline = campaign_key(campaign_inputs)
    assert campaign_key(campaign_inputs, analyser_root=source_trees["analyser"]) == baseline
    pipeline = source_trees["analyser"] / "bat_analyser" / "spectrogram_pipeline.py"
    pipeline.write_text("render = 2", encoding="utf-8")
    assert campaign_key(campaign_inputs) != baseline
    baseline = campaign_key(campaign_inputs)
    pipeline.unlink()
    assert campaign_key(campaign_inputs) != baseline


@pytest.mark.parametrize("package", ["bat_analysis", "benchmark"])
def test_external_detector_and_scoring_invalidate(source_trees, package):
    root = source_trees["processing"]
    baseline = runner.code_fingerprint(root)
    (root / package / "__init__.py").write_text("value = 2", encoding="utf-8")
    assert runner.code_fingerprint(root) != baseline


@pytest.mark.parametrize("runtime", [
    {"packages": [("plotly", "changed")]}, {"python": "changed"},
])
def test_dependency_and_python_changes_invalidate_both_caches(
        source_trees, campaign_inputs, monkeypatch, runtime):
    baseline = runner.code_fingerprint(source_trees["processing"])
    campaign = campaign_key(campaign_inputs)
    monkeypatch.setattr(fingerprints, "runtime_identity", lambda: runtime)
    assert runner.code_fingerprint(source_trees["processing"]) != baseline
    assert campaign_key(campaign_inputs) != campaign


def test_runtime_records_all_installed_dependency_versions(monkeypatch):
    distributions = [SimpleNamespace(metadata={"Name": "Plotly"}, version="1"),
                     SimpleNamespace(metadata={"Name": "threadpoolctl"}, version="2")]
    monkeypatch.setattr(fingerprints.importlib.metadata, "distributions", lambda: distributions)
    assert fingerprints.runtime_identity()["packages"] == [("plotly", "1"), ("threadpoolctl", "2")]
    distributions[0].version = "3"
    assert fingerprints.runtime_identity()["packages"][0] == ("plotly", "3")


@pytest.mark.parametrize("change", ["parameters", "audio", "annotations", "code", "dependencies"])
def test_runner_reuses_only_identical_experiments(tmp_path, source_trees, monkeypatch, change):
    from laboratory import report

    external = source_trees["processing"]
    (external / "bat_analysis" / "detection.py").write_text("value = 1", encoding="utf-8")
    evaluator = external / "benchmark" / "reassignment_detector_seeds.py"
    evaluator.write_text("def evaluate(geometry_mode=None): pass", encoding="utf-8")
    wav_root = tmp_path / "wavs"
    wav_root.mkdir()
    (wav_root / "sample.wav").write_bytes(b"first")
    annotations = tmp_path / "annotations.json"
    annotations.write_text(json.dumps({"files": {"sample.wav": {"chirps": []}}}), encoding="utf-8")
    calls = []

    def fake_worker(name, record, wav_dir, options, processing_root):
        calls.append(name)
        return dict(file=name, elapsed_s=0, details=[],
                    per_file=[dict(file=name, annotated=0, candidates=0, matched=0)])

    monkeypatch.setattr(runner, "_worker", fake_worker)
    monkeypatch.setattr(report, "write_html", lambda data, path: path.write_text("report", encoding="utf-8"))
    settings = dict(annotations=annotations, wav_dir=wav_root, out=tmp_path / "results",
                    workers=1, processing_root=external)
    runner.run(**settings)
    assert runner.run(**settings)["metadata"]["files_from_cache"] == 1
    if change == "parameters":
        settings["options"] = {"search_margin_ms": 3.0}
    elif change == "audio":
        (wav_root / "sample.wav").write_bytes(b"other")
    elif change == "annotations":
        annotations.write_text(json.dumps({"files": {"sample.wav": {"chirps": [], "review": "changed"}}}), encoding="utf-8")
    elif change == "code":
        evaluator.write_text("def evaluate(geometry_mode=None): return None", encoding="utf-8")
    else:
        monkeypatch.setattr(fingerprints, "runtime_identity", lambda: {"packages": [("numpy", "next")]})
    assert runner.run(**settings)["metadata"]["files_from_cache"] == 0
    assert calls == ["sample.wav", "sample.wav"]


@pytest.mark.parametrize("change", ["audio", "annotations", "scoring", "rendering", "dependency", "window"])
def test_campaign_reexecutes_only_when_acoustic_identity_changes(
        source_trees, campaign_inputs, tmp_path, monkeypatch, change):
    calls = []

    def fake_run(command, **kwargs):
        calls.append(command)
        output = Path(command[command.index("--output") + 1])
        row = dict(scenario="global", region="all", error="",
                   baseline_local_score_eq="0.4", perturbed_local_score_eq="2.0",
                   baseline_priority="no_alert_unverified",
                   perturbed_priority="review_suggested")
        with (output / "sensitivity_cases.csv").open("w", newline="", encoding="utf-8") as stream:
            writer = csv.DictWriter(stream, fieldnames=row)
            writer.writeheader()
            writer.writerow(row)
        return SimpleNamespace(returncode=0)

    monkeypatch.setattr(local_campaign.subprocess, "run", fake_run)
    output = tmp_path / "results"
    settings = dict(max_chirps=1, skip_chirps=0, windows=(5,), thresholds=(1.5,))
    first = local_campaign.run_campaign(campaign_inputs, output, **settings)
    settings["thresholds"] = (2.5,)
    resumed = local_campaign.run_campaign(campaign_inputs, output, **settings)
    assert len(calls) == 1
    assert resumed["runs"][0]["state"] == "completed_cached"
    summary = json.loads((output / "campaign_summary.json").read_text(encoding="utf-8"))
    assert summary["results"][0]["threshold"] == 2.5
    assert summary["results"][0]["new_alerts"] == 0
    if change in ("audio", "annotations"):
        name = "audio.wav" if change == "audio" else "manual.json"
        (campaign_inputs / name).write_bytes(b"changed")
    elif change == "scoring":
        (source_trees["laboratory"] / "chirp_geometry" / "__init__.py").write_text("changed")
    elif change == "rendering":
        (source_trees["analyser"] / "bat_analyser" / "__init__.py").write_text("changed")
    elif change == "dependency":
        monkeypatch.setattr(fingerprints, "runtime_identity", lambda: {"packages": [("scipy", "next")]})
    else:
        settings["windows"] = (7,)
    rerun = local_campaign.run_campaign(campaign_inputs, output, **settings)
    assert len(calls) == 2
    assert rerun["runs"][0]["run_id"] != first["runs"][0]["run_id"]
    assert rerun["runs"][0]["state"] == "completed"
