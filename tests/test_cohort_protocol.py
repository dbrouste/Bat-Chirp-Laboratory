"""Synthetic cohort checks; no real corpus, detector, or spectral processing."""

import csv
import json
from pathlib import Path
import wave

import pytest

from laboratory import cohort_protocol as cohorts
from laboratory.dataset import sha256_file, stable_hash


def manual(chirp_id=1):
    return {"chirp_id": chirp_id, "source": "manual", "status": "validated",
            "points": [{"t_ms": 1, "f_khz": 80}, {"t_ms": 3, "f_khz": 60}]}


@pytest.fixture
def corpus(tmp_path):
    dataset = tmp_path / "dataset"
    rows = []
    for session, partition in (("alpha", "calibration"), ("beta", "calibration"),
                               ("gamma", "calibration"), ("sealed", "test")):
        files = {}
        for name, record in (("positive.wav", {"chirps": [manual()]}),
                             ("negative.wav", {"review_status": "no_chirp", "chirps": []}),
                             ("unknown.wav", {"chirps": []})):
            relative = f"{partition}/wav/{session}/{name}"
            path = dataset / relative
            path.parent.mkdir(parents=True, exist_ok=True)
            with wave.open(str(path), "wb") as stream:
                stream.setnchannels(1)
                stream.setsampwidth(2)
                stream.setframerate(192000)
                stream.writeframes(bytes.fromhex(stable_hash([session, name])) * 120)
            files[name] = record
            rows.append({"partition": partition, "session": session,
                         "dataset_wav": relative,
                         "dataset_json": f"{partition}/annotations/{session}/{session}.json",
                         "wav_sha256": sha256_file(path), "json_sha256": ""})
        annotation = dataset / rows[-1]["dataset_json"]
        annotation.parent.mkdir(parents=True)
        annotation.write_text(json.dumps({"schema_version": 1,
            "created_by": "Bat-Acoustic-Analyser", "units": {"time": "ms", "frequency": "kHz"},
            "files": files}), encoding="utf-8")
        for row in rows:
            if row["session"] == session:
                row["json_sha256"] = sha256_file(annotation)
    write_manifest(dataset, rows)
    return dataset, rows


def write_manifest(dataset, rows):
    with (dataset / "manifest.csv").open("w", newline="", encoding="utf-8") as stream:
        writer = csv.DictWriter(stream, fieldnames=rows[0])
        writer.writeheader()
        writer.writerows(rows)


def change_record(dataset, rows, session, name, record):
    row = next(row for row in rows if row["session"] == session)
    annotation = dataset / row["dataset_json"]
    document = json.loads(annotation.read_text(encoding="utf-8"))
    document["files"][name] = record
    annotation.write_text(json.dumps(document), encoding="utf-8")
    for member in rows:
        if member["dataset_json"] == row["dataset_json"]:
            member["json_sha256"] = sha256_file(annotation)
    write_manifest(dataset, rows)


def test_deterministic_grouped_snapshot_and_closed_final_test(corpus, tmp_path, monkeypatch):
    dataset, rows = corpus
    before = {path: path.read_bytes() for path in dataset.rglob("*") if path.is_file()}
    original_header = cohorts.wav_header

    def guarded_header(path):
        assert "test" not in path.relative_to(dataset).parts
        return original_header(path)

    monkeypatch.setattr(cohorts, "wav_header", guarded_header)
    for row in rows:
        if row["partition"] == "test":
            (dataset / row["dataset_wav"]).unlink()
            (dataset / row["dataset_json"]).unlink(missing_ok=True)
    output = tmp_path / "report"
    report = cohorts.audit_cohorts(dataset, output)
    snapshot = json.loads((output / "cohort_snapshot.json").read_text(encoding="utf-8"))
    write_manifest(dataset, list(reversed(rows)))
    second_output = tmp_path / "second"
    cohorts.audit_cohorts(dataset, second_output)
    second = json.loads((second_output / "cohort_snapshot.json").read_text(encoding="utf-8"))
    assert snapshot["assignments"] == second["assignments"]
    assert snapshot["snapshot_id"] != second["snapshot_id"]
    for session in {row["session"] for row in rows}:
        assert len({row["cohort"] for row in snapshot["assignments"] if row["session"] == session}) == 1
    assert report["cohorts"]["final_test_reserved"]["inspected_wavs"] == 0
    assert report["test_files_opened"] is False
    assert sum(group.get("reference_classes", {}).get("reviewed_no_chirp", 0)
               for group in report["cohorts"].values()) == 3
    for path, content in before.items():
        if path.exists() and path.name != "manifest.csv":
            assert path.read_bytes() == content


def test_duplicate_audio_groups_never_cross_cohorts(corpus):
    dataset, rows = corpus
    rows[3]["wav_sha256"] = rows[0]["wav_sha256"]
    assignments = cohorts.assign_cohorts(rows, cohorts.read_protocol(cohorts.DEFAULT_PROTOCOL))
    assert len({row["cohort"] for row in assignments if row["session"] in {"alpha", "beta"}}) == 1
    rows[-1]["wav_sha256"] = rows[0]["wav_sha256"]
    with pytest.raises(ValueError, match="crosses"):
        cohorts.assign_cohorts(rows, cohorts.read_protocol(cohorts.DEFAULT_PROTOCOL))


def test_insufficient_groups_requires_decision(corpus):
    _, rows = corpus
    with pytest.raises(ValueError, match="Insufficient"):
        cohorts.assign_cohorts(rows[:3], cohorts.read_protocol(cohorts.DEFAULT_PROTOCOL))


@pytest.mark.parametrize("record,expected", [
    ({"chirps": []}, "unknown"),
    ({"chirps": [{"source": "automatic", "status": "proposed"}]}, "unknown"),
    ({"review_status": "no_chirp", "chirps": []}, "reviewed_no_chirp"),
    ({"review_status": "no_chirp", "chirps": [{"source": "automatic"}]}, "reviewed_no_chirp"),
    ({"review_status": "no_chirp", "chirps": [manual()]}, "ambiguous"),
    ({"review_status": "no_chirp", "chirps": [{"edited": True}]}, "ambiguous"),
    ({"review_status": "no_chirp", "chirps": [{"source": "automatic", "status": "validated"}]}, "ambiguous"),
])
def test_only_explicit_consistent_negatives_are_eligible(corpus, record, expected):
    dataset, rows = corpus
    change_record(dataset, rows, "alpha", "unknown.wav", record)
    assignments = cohorts.assign_cohorts(rows, cohorts.read_protocol(cohorts.DEFAULT_PROTOCOL))
    wavs, _, findings, _ = cohorts.inspect_calibration(dataset, assignments)
    selected = next(row for row in wavs if row["session"] == "alpha" and row["file"].endswith("unknown.wav"))
    assert selected["reference_class"] == expected
    assert any(row["issue"] == "contradictory_no_chirp_label" for row in findings) == (expected == "ambiguous")


def test_exclusions_and_invalid_geometry_remain_explicit(corpus, tmp_path):
    dataset, rows = corpus
    excluded = {**manual(), "annotation_quality": {"status": "excluded"}}
    invalid = {**manual(2), "points": [{"t_ms": 1, "f_khz": 80}]}
    change_record(dataset, rows, "alpha", "positive.wav", {"chirps": [excluded, invalid]})
    report = cohorts.audit_cohorts(dataset, tmp_path / "out")
    assert report["sessions"]["alpha"]["positive_references"] == 1
    assert any("invalid_points" in item["issue"] for item in report["findings"])


def test_annotation_hash_and_output_protections(corpus, tmp_path):
    dataset, rows = corpus
    with pytest.raises(ValueError, match="outside"):
        cohorts.audit_cohorts(dataset, dataset / "out")
    existing = tmp_path / "existing"
    existing.mkdir()
    with pytest.raises(ValueError, match="never overwritten"):
        cohorts.audit_cohorts(dataset, existing)
    annotation = dataset / rows[0]["dataset_json"]
    annotation.write_bytes(annotation.read_bytes() + b" ")
    with pytest.raises(ValueError, match="hash mismatch"):
        cohorts.audit_cohorts(dataset, tmp_path / "out")


@pytest.mark.parametrize("change", ["traversal", "duplicate", "session_crossing", "hash"])
def test_manifest_rejects_ambiguous_or_unsafe_inputs(corpus, change):
    dataset, rows = corpus
    if change == "traversal":
        rows[0]["dataset_wav"] = "../escape.wav"
    elif change == "duplicate":
        rows.append(dict(rows[0]))
    elif change == "hash":
        rows[0]["wav_sha256"] = ""
    else:
        rows[-1]["session"] = rows[0]["session"]
    write_manifest(dataset, rows)
    with pytest.raises(ValueError):
        cohorts.assign_cohorts(cohorts.read_manifest(dataset), cohorts.read_protocol(cohorts.DEFAULT_PROTOCOL))


def benchmark_file(dataset, rows, tmp_path):
    details, hashes = [], {}
    for row in rows:
        if row["partition"] != "calibration" or not row["dataset_wav"].endswith("positive.wav"):
            continue
        name = row["dataset_wav"]
        record = json.loads((dataset / row["dataset_json"]).read_text(encoding="utf-8"))["files"]["positive.wav"]
        hashes[name] = {"wav_sha256": row["wav_sha256"], "annotation_sha256": stable_hash(record)}
        details.append({"file": name, "chirp_id": 1, "snr_max_db": [15, 25, 35][len(details)],
                        "detector_matched": False, "tracking_success": False})
    report = {"metadata": {"status": "complete", "scoring_protocol": "square_normals_v1",
                           "input_fingerprints": hashes, "frozen_dataset": {
                               "manifest_sha256": sha256_file(dataset / "manifest.csv"),
                               "partition": "calibration", "hashes_verified": True}}, "details": details}
    path = tmp_path / "benchmark.json"
    path.write_text(json.dumps(report), encoding="utf-8")
    return path, report


def test_existing_snr_metrics_are_imported_without_audio_processing(corpus, tmp_path):
    dataset, rows = corpus
    path, _ = benchmark_file(dataset, rows, tmp_path)
    report = cohorts.audit_cohorts(dataset, tmp_path / "out", benchmark_report=path)
    assert sum(group.get("snr_measured", 0) for group in report["cohorts"].values()) == 3
    assert sum(session["snr_distribution"]["low_15_db_or_less"] for session in report["sessions"].values()) == 1
    assert sum(session["snr_distribution"]["high_35_db_or_more"] for session in report["sessions"].values()) == 1
    assert report["benchmark"]["role"] == "retrospective_descriptive_only"


@pytest.mark.parametrize("change", ["manifest", "annotations", "audio", "test", "duplicate", "snr", "protocol"])
def test_stale_or_ambiguous_benchmark_is_rejected(corpus, tmp_path, change):
    dataset, rows = corpus
    path, report = benchmark_file(dataset, rows, tmp_path)
    name = report["details"][0]["file"]
    if change == "manifest":
        report["metadata"]["frozen_dataset"]["manifest_sha256"] = "old"
    elif change in {"annotations", "audio"}:
        field = "annotation_sha256" if change == "annotations" else "wav_sha256"
        report["metadata"]["input_fingerprints"][name][field] = "old"
    elif change == "test":
        report["details"][0]["file"] = rows[-1]["dataset_wav"]
    elif change == "duplicate":
        report["details"].append(report["details"][0])
    elif change == "protocol":
        report["metadata"]["scoring_protocol"] = "vertical_legacy"
    else:
        report["details"][0]["snr_max_db"] = float("nan")
    path.write_text(json.dumps(report), encoding="utf-8")
    with pytest.raises(ValueError):
        cohorts.audit_cohorts(dataset, tmp_path / "out", benchmark_report=path)


def add_negative_results(dataset, rows, report):
    report["metadata"]["include_reviewed_negatives"] = True
    report["per_file"] = []
    for row in rows:
        if row["partition"] != "calibration" or not row["dataset_wav"].endswith("negative.wav"):
            continue
        name = row["dataset_wav"]
        report["metadata"]["input_fingerprints"][name] = {
            "wav_sha256": row["wav_sha256"],
            "annotation_sha256": stable_hash({"review_status": "no_chirp", "chirps": []})}
        report["per_file"].append({"file": name, "reference_class": "reviewed_no_chirp",
                                   "candidates": len(report["per_file"])})


def test_negative_metrics_apply_only_to_explicit_negatives(corpus, tmp_path):
    dataset, rows = corpus
    path, benchmark = benchmark_file(dataset, rows, tmp_path)
    add_negative_results(dataset, rows, benchmark)
    path.write_text(json.dumps(benchmark), encoding="utf-8")
    report = cohorts.audit_cohorts(dataset, tmp_path / "out", benchmark_report=path)
    summaries = [report["cohorts"][name]["negative_evaluation"] for name in ("development", "validation")]
    assert sum(summary["evaluated_wavs"] for summary in summaries) == 3
    assert sum(summary["candidate_alerts"] for summary in summaries) == 3
    assert all(summary["missing_wavs"] == 0 for summary in summaries)
    assert all(summary["candidate_alerts_per_hour"] is not None for summary in summaries)


@pytest.mark.parametrize("count", [-1, 1.5, True, None])
def test_invalid_negative_counts_are_not_silently_zero(corpus, tmp_path, count):
    dataset, rows = corpus
    path, benchmark = benchmark_file(dataset, rows, tmp_path)
    add_negative_results(dataset, rows, benchmark)
    benchmark["per_file"][0]["candidates"] = count
    path.write_text(json.dumps(benchmark), encoding="utf-8")
    with pytest.raises(ValueError, match="candidate count"):
        cohorts.audit_cohorts(dataset, tmp_path / "out", benchmark_report=path)


def test_protocol_configuration_is_recorded_and_versions_snapshot(corpus, tmp_path):
    dataset, _ = corpus
    first = tmp_path / "first"
    cohorts.audit_cohorts(dataset, first)
    snapshot = json.loads((first / "cohort_snapshot.json").read_text(encoding="utf-8"))
    second = tmp_path / "second"
    cohorts.audit_cohorts(dataset, second)
    assert snapshot == json.loads((second / "cohort_snapshot.json").read_text(encoding="utf-8"))
    protocol = cohorts.read_protocol(cohorts.DEFAULT_PROTOCOL)
    protocol["validation_groups"] = 2
    protocol_file = tmp_path / "protocol.json"
    protocol_file.write_text(json.dumps(protocol), encoding="utf-8")
    third = tmp_path / "third"
    report = cohorts.audit_cohorts(dataset, third, protocol_path=protocol_file)
    assert report["snapshot_id"] != snapshot["snapshot_id"]
    assert report["cohorts"]["validation"]["session_groups"] == 2
    assert report["status"] == "draft_not_authorized_for_model_selection"


def test_unknown_protocol_policy_is_refused(tmp_path):
    protocol = cohorts.read_protocol(cohorts.DEFAULT_PROTOCOL)
    protocol["test_policy"] = "open_test"
    path = tmp_path / "protocol.json"
    path.write_text(json.dumps(protocol), encoding="utf-8")
    with pytest.raises(ValueError, match="Unsupported"):
        cohorts.read_protocol(path)


def test_missing_snr_and_missing_headers_remain_missing(corpus, tmp_path, monkeypatch):
    dataset, _ = corpus
    monkeypatch.setattr(cohorts, "wav_header", lambda path: (None, None))
    report = cohorts.audit_cohorts(dataset, tmp_path / "out")
    for name in ("development", "validation"):
        summary = report["cohorts"][name]
        assert summary["snr_missing"] == summary["positive_references"]
        assert summary["wav_duration_missing"] == summary["manifest_wavs"]
        assert summary["negative_evaluation"]["candidate_alerts"] is None
        assert summary["negative_evaluation"]["candidate_alerts_per_hour"] is None
