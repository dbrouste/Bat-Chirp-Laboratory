"""Development-only selection tests; reserved files deliberately do not exist."""

import csv
import json

import pytest

from laboratory import campaign_control, global_campaign, pilot_selection
from laboratory.dataset import sha256_file, stable_hash
from laboratory.frozen_dataset import collect_frozen_dataset


@pytest.fixture
def selected_corpus(tmp_path):
    dataset = tmp_path / "input"
    campaign_control.synthetic_dataset(dataset)
    with (dataset / "manifest.csv").open(encoding="utf-8", newline="") as stream:
        rows = list(csv.DictReader(stream))
    for partition, session, digest in (("calibration", "validation", "b"), ("test", "sealed", "c")):
        rows.append(dict(partition=partition, session=session, dataset_wav=f"{partition}/wav/{session}/absent.wav",
                         dataset_json=f"{partition}/annotations/{session}/absent.json", wav_sha256=digest*64, json_sha256=digest*64))
    with (dataset / "manifest.csv").open("w", encoding="utf-8", newline="") as stream:
        writer = csv.DictWriter(stream, fieldnames=rows[0])
        writer.writeheader()
        writer.writerows(rows)
    assignments = [{**row, "cohort": "development" if row["session"] == "synthetic" else
                    "validation" if row["session"] == "validation" else "final_test_reserved"} for row in rows]
    snapshot = {"schema": "chirp_cohort_snapshot_v1", "manifest_sha256": sha256_file(dataset / "manifest.csv"),
                "assignments": assignments}
    snapshot["snapshot_id"] = stable_hash(snapshot)
    path = tmp_path / "snapshot.json"
    path.write_text(json.dumps(snapshot), encoding="utf-8")
    output = tmp_path / "selection"
    result = pilot_selection.prepare_selection(dataset, path, output)
    return dataset, path, output / "selection.json", result


def test_shortfalls_are_explicit_and_reserved_files_never_opened(selected_corpus):
    dataset, _, selection_path, result = selected_corpus
    assert result["requested"] == {"positive": 20, "negative": 20}
    assert result["available"] == result["selected"] == {"positive": 1, "negative": 1}
    records, provenance = collect_frozen_dataset(dataset, selection_file=selection_path)
    assert len(records) == 2 and provenance["cohort"] == "development"
    assert all("synthetic" in name for name in records)


def test_selection_is_deterministic(selected_corpus, tmp_path):
    dataset, snapshot, _, original = selected_corpus
    repeated = pilot_selection.prepare_selection(dataset, snapshot, tmp_path / "second")
    assert original == repeated


def test_injecting_a_validation_file_is_rejected(selected_corpus):
    dataset, _, path, selection = selected_corpus
    selection["files"].append({"file": "calibration/wav/validation/absent.wav", "reference_class": "reviewed_no_chirp"})
    selection["fingerprint"] = stable_hash({key: value for key, value in selection.items() if key != "fingerprint"})
    path.write_text(json.dumps(selection), encoding="utf-8")
    with pytest.raises(ValueError, match="development"):
        collect_frozen_dataset(dataset, selection_file=path)


def test_unselected_wavs_in_shared_json_are_not_read(selected_corpus):
    dataset, _, path, selection = selected_corpus
    negative = next(row for row in selection["files"] if row["reference_class"] == "reviewed_no_chirp")
    (dataset / negative["file"]).unlink()
    selection["files"] = [row for row in selection["files"] if row["reference_class"] == "positive_partial"]
    selection["fingerprint"] = stable_hash({key: value for key, value in selection.items() if key != "fingerprint"})
    path.write_text(json.dumps(selection), encoding="utf-8")
    records, _ = collect_frozen_dataset(dataset, selection_file=path)
    assert len(records) == 1


def test_changed_snapshot_or_manifest_is_rejected(selected_corpus):
    dataset, snapshot, selection, _ = selected_corpus
    snapshot.write_bytes(snapshot.read_bytes() + b" ")
    with pytest.raises(ValueError, match="Snapshot file changed"):
        collect_frozen_dataset(dataset, selection_file=selection)


@pytest.mark.parametrize("record", [
    {"review_status": "no_chirp", "chirps": [{"source": "manual"}]},
    {"review_status": "no_chirp", "chirps": [{"status": "validated"}]},
    {"review_status": "no_chirp", "chirps": [{"edited": True}]},
])
def test_conflicting_negatives_cannot_be_selected(record):
    with pytest.raises(ValueError, match="Contradictory"):
        pilot_selection.reference_class(record)


def test_missing_annotations_are_unknown_not_negative():
    assert pilot_selection.reference_class({"chirps": []}) == "unknown"


def test_session_balancing_does_not_invent_references():
    rows = [{"file": f"{session}/{index}", "session": session, "snr_stratum": "unknown"}
            for session in ("one", "two", "three") for index in range(10)]
    chosen = pilot_selection.balanced(rows, 6, "seed")
    assert all(sum(row["session"] == session for row in chosen) == 2 for session in ("one", "two", "three"))


def test_global_budget_charges_an_unknown_interrupted_session(tmp_path):
    directory = tmp_path / "global_runs" / "run"
    directory.mkdir(parents=True)
    (directory / "reservation.json").write_text('{"reserved_s":600}', encoding="utf-8")
    assert global_campaign.usage(tmp_path)[0]["reserved_s"] == 600
    (directory / "outcome.json").write_text('{"elapsed_s":2,"status":"interrupted"}', encoding="utf-8")
    row = global_campaign.usage(tmp_path)[0]
    assert row.get("elapsed_s", row["reserved_s"]) == 2
