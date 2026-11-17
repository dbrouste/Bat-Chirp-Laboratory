"""Review exports use stored evidence and isolate all analyser annotation writes."""
import copy
import csv
import importlib.util
import json
from pathlib import Path
import sqlite3
import sys

import pytest

from laboratory.dataset import sha256_file
from tools import prepare_scientific_review as review


@pytest.fixture
def evidence(tmp_path):
    dataset = tmp_path / "dataset"
    campaign = tmp_path / "campaign"
    campaign.mkdir()
    positive, negative = "calibration/wav/A/same.wav", "calibration/wav/B/same.wav"
    records = {positive: {"chirps": [{"chirp_id": i, "source": "manual", "status": "validated",
                 "points": [{"t_ms": 100 + i, "f_khz": 50}, {"t_ms": 101 + i, "f_khz": 40}]}
                 for i in range(1, 26)]}, negative: {"review_status": "no_chirp", "chirps": []}}
    sources, manifest = {}, {}
    for name in records:
        path = dataset / name
        path.parent.mkdir(parents=True)
        path.write_bytes((name + ' synthetic bytes, no decoding allowed').encode())
        sources[name] = {"file": name, "session": name.split('/')[-2], "source_wav": str(tmp_path / 'originals' / name),
                         "source_json": str(tmp_path / 'originals' / (name + '.json')),
                         "wav_sha256": sha256_file(path),
                         "reference_class": "positive_partial" if name == positive else "reviewed_no_chirp"}
        manifest[name] = {"dataset_wav": name, "dataset_json": name.replace('/wav/', '/annotations/') + '.json'}
    details = [{"file": positive, "chirp_id": i, "end_error_ms": float(i), "snr_max_db": 30.0} for i in range(1, 26)]
    suppressed = {"uid": "raw:0:12", "time_mid": 0.102, "peak_freq_hz": 45000.0}
    final = {"uid": "raw:0:13", "time_mid": 0.103, "peak_freq_hz": 46000.0}
    failures = [{"file": positive, "chirp_id": 1, "category": "no_candidate_observed", "last_stage": "spectral_blobs", "reason": "no_blob"},
                {"file": positive, "chirp_id": 2, "category": "known_rejection", "last_stage": "time_frequency_nms", "reason": "nms",
                 "evidence": {"pre_nms": [suppressed]}}]
    endpoints = [{"file": positive, "chirp_id": i, "tracking_success": True, "end_abs_ms": float(i),
                  "end_signed_ms": float(-i if i % 2 else i), "start_signed_ms": 0.0,
                  "trace": {"curve_hash": str(i), "start_s": .1, "end_s": .2, "start_hz": 50000, "end_hz": 40000}}
                 for i in range(3, 26)]
    negative_result = {"status": "complete", "accepted_final_trajectories": 1, "duration_seconds": 1.0,
                       "candidates": 2, "tracking_traces": [], "scope": "native_not_gui",
                       "accepted_curves": [{"source_candidate_indices": [0, 1],
                                            "points": [[.25, 50000.0, .1], [.3, 40000.0, .2]]}]}
    report = {"totals": {"matched": 23}, "details": details, "diagnostics": {
        positive: {"failures": failures, "endpoints": endpoints, "negative": None,
                   "stages": {"final": [final], "nms_input": [suppressed, final]}},
        negative: {"failures": [], "endpoints": [], "negative": negative_result}}}
    report_path = campaign / 'jobs' / 'baseline' / 'benchmark' / 'report.json'
    report_path.parent.mkdir(parents=True)
    report_path.write_text(json.dumps(report), encoding='utf-8')
    definition = {"dataset": str(dataset), "processing_root": str(tmp_path / 'processing'),
                  "baseline_identity": "fixed-baseline", "fingerprint": "definition", "code_fingerprint": "scientific-code",
                  "selection_file": "selection.json", "diagnostic_probe": True, "partition": "calibration",
                  "test_access": False, "experiments": [{"job_id": "baseline", "options": {}}]}
    selection = {"files": list(sources.values()), "fingerprint": "selection"}
    return definition, selection, records, manifest, report, report_path


def cases(data):
    definition, selection, records, manifest, report, _ = data
    return review.make_cases(report, records, {row['file']: row for row in selection['files']}, manifest, definition['baseline_identity'])


def test_top_twenty_are_deterministic_and_keep_signed_errors(evidence):
    first = cases(evidence)
    second = copy.deepcopy(evidence)
    second[4]['diagnostics'] = dict(reversed(list(second[4]['diagnostics'].items())))
    second[4]['diagnostics'][next(iter(evidence[2]))]['endpoints'].reverse()
    assert cases(second) == first
    top = [row for row in first if row['queue'] == 'end_error_top20']
    assert [row['manual_chirp_id'] for row in top] == list(range(25, 5, -1))
    assert top[0]['metrics']['end_signed_ms'] == -25
    assert top[0]['automatic']['full_trajectory_available'] is False
    assert top[0]['automatic']['representation'] == 'endpoints_only_no_connecting_curve'


def test_nms_exports_observed_witnesses_not_an_invented_suppressor(evidence):
    row = next(row for row in cases(evidence) if row['queue'] == 'nms')
    assert row['evidence']['suppressed_candidates'][0]['uid'] == 'raw:0:12'
    assert row['evidence']['suppressor_identity_recorded'] is False
    assert row['evidence']['candidate_anchor_points'][0]['t_ms'] == pytest.approx(102)
    assert row['manual_annotation']['points'][0]['f_khz'] == 50


def test_negative_points_remain_full_and_never_become_manual_references(evidence):
    row = next(row for row in cases(evidence) if row['queue'] == 'negative_final')
    assert row['manual_chirp_id'] is None and row['manual_annotation'] is None
    assert row['automatic']['points'] == [{'t_ms': 250., 'f_khz': 50.}, {'t_ms': 300., 'f_khz': 40.}]
    assert row['automatic']['native_points_s_hz_amplitude'][0] == [.25, 50000., .1]
    evidence[2][row['file']].pop('review_status')
    with pytest.raises(ValueError, match='no_chirp'):
        cases(evidence)


def test_unselected_cases_and_missing_manual_references_fail_closed(evidence):
    wrong = copy.deepcopy(evidence)
    wrong[4]['diagnostics']['validation/wav/forbidden.wav'] = {'failures': []}
    with pytest.raises(ValueError, match='outside development'):
        cases(wrong)
    evidence[2][next(iter(evidence[2]))]['chirps'].pop(0)
    with pytest.raises(ValueError, match='manual reference'):
        cases(evidence)


def test_export_copies_are_isolated_and_import_columns_are_compatible(evidence, tmp_path, monkeypatch):
    before = copy.deepcopy(evidence)
    monkeypatch.setattr(review, 'load_evidence', lambda path: evidence)
    monkeypatch.setattr(review.control, 'verify_environment', lambda definition: None)
    output = tmp_path / 'review'
    result = review.export_review(evidence[-1].parents[3], output, min_free_gb=.001)
    assert result['counts'] == {'no_blob': 1, 'nms': 1, 'end_error_top20': 20, 'negative_final': 1}
    assert result['scientific_calculations_executed'] is False
    assert evidence == before
    for queue in review.QUEUES:
        with (output / f'{queue}.analyser.csv').open(encoding='utf-8-sig', newline='') as f:
            rows = list(csv.DictReader(f))
        assert {'wav', 'annotation', 'partition', 'session', 'chirp_id'}.issubset(rows[0])
        for row in rows:
            assert (output / 'analyser_workspace' / row['wav']).is_file()
            assert (output / 'analyser_workspace' / row['annotation']).is_file()
    with (output / 'analyser_workspace/manifest.csv').open(encoding='utf-8-sig', newline='') as f:
        mappings = list(csv.DictReader(f))
    assert len(mappings) == 2 and mappings[0]['source_wav'] != mappings[1]['source_wav']
    copied_wav = Path(mappings[0]['source_wav'])
    source_wav = Path(evidence[0]['dataset']) / next(iter(evidence[2]))
    original_hash = sha256_file(source_wav)
    copied_wav.write_bytes(b'changed disposable review audio')
    assert sha256_file(source_wav) == original_hash
    working = Path(mappings[0]['source_json'])
    snapshot = output / 'analyser_workspace' / mappings[0]['dataset_json']
    working.write_text('{}', encoding='utf-8')
    assert snapshot.read_text(encoding='utf-8') != '{}'
    assert (Path(evidence[0]['dataset']) / mappings[0]['dataset_wav']).exists() is False
    assert all(row['signal_classification'] == '' for row in csv.DictReader((output/'human_decisions.csv').open(encoding='utf-8-sig')))
    with pytest.raises(ValueError, match='never overwritten'):
        review.export_review(evidence[-1].parents[3], output)
    assert working.read_text(encoding='utf-8') == '{}'


@pytest.mark.parametrize('failure', ['changed_code', 'changed_report', 'held_out', 'incomplete'])
def test_validity_gate_blocks_stale_or_forbidden_evidence(evidence, monkeypatch, failure):
    definition, selection, records, manifest, report, report_path = evidence
    campaign = report_path.parents[3]
    with sqlite3.connect(campaign/'campaign.sqlite') as connection:
        connection.executescript('CREATE TABLE detector_control(singleton INT,status TEXT); CREATE TABLE detector_jobs(job_id TEXT,status TEXT,report_sha256 TEXT,ordinal INT);')
        connection.execute('INSERT INTO detector_control VALUES(1,?)', ('planned' if failure=='incomplete' else 'complete',))
        connection.execute('INSERT INTO detector_jobs VALUES(?,?,?,0)', ('baseline','complete',sha256_file(report_path)))
    monkeypatch.setattr(review.control, 'load_definition', lambda path: definition)
    monkeypatch.setattr(review, 'load_selection', lambda *args: selection)
    def verify(definition):
        if failure == 'changed_code':
            raise ValueError('Code or environment changed')
    monkeypatch.setattr(review.control, 'verify_environment', verify)
    if failure == 'changed_report':
        report_path.write_text('{}', encoding='utf-8')
    if failure == 'held_out':
        definition['test_access'] = True
    with pytest.raises(ValueError):
        review.load_evidence(campaign)


def test_copy_budget_fails_before_creating_output(evidence, tmp_path, monkeypatch):
    monkeypatch.setattr(review, 'load_evidence', lambda path: evidence)
    output = tmp_path/'review'
    with pytest.raises(ValueError, match='copy budget'):
        review.export_review(evidence[-1].parents[3], output, max_copy_gb=1e-12)
    assert not output.exists()


def test_rank_ties_have_stable_file_and_chirp_order(evidence):
    endpoints = next(iter(evidence[4]['diagnostics'].values()))['endpoints']
    for row in endpoints:
        row['end_abs_ms'] = 10.
    first = [row['case_id'] for row in cases(evidence) if row['queue'] == 'end_error_top20']
    endpoints.reverse()
    assert [row['case_id'] for row in cases(evidence) if row['queue'] == 'end_error_top20'] == first


def test_existing_decisions_are_not_touched_or_even_reloaded(tmp_path, monkeypatch):
    output = tmp_path/'review'
    output.mkdir()
    decisions = output/'human_decisions.csv'
    decisions.write_text('human decision must survive', encoding='utf-8')
    monkeypatch.setattr(review, 'load_evidence', lambda *args: pytest.fail('No input read is needed to reject overwrite'))
    with pytest.raises(ValueError, match='never overwritten'):
        review.export_review(tmp_path/'campaign', output)
    assert decisions.read_text(encoding='utf-8') == 'human decision must survive'


@pytest.mark.integration
def test_existing_analyser_resolver_accepts_all_review_queues(evidence, tmp_path, monkeypatch):
    source = Path(__file__).resolve().parents[2]/'Bat-Acoustic-Analyser/bat_analyser/annotation_quality.py'
    if not source.is_file():
        pytest.skip('Sibling analyser checkout required; synthetic data only')
    spec = importlib.util.spec_from_file_location('review_consumer_check', source)
    consumer = importlib.util.module_from_spec(spec)
    monkeypatch.setitem(sys.modules, spec.name, consumer)
    spec.loader.exec_module(consumer)
    monkeypatch.setattr(review, 'load_evidence', lambda path: evidence)
    monkeypatch.setattr(review.control, 'verify_environment', lambda definition: None)
    output = tmp_path/'review'
    review.export_review(evidence[-1].parents[3], output, min_free_gb=.001)
    for queue in review.QUEUES:
        session = consumer.QualityReviewSession.from_acoustic_csv(output/f'{queue}.analyser.csv',
                                      manifest_csv=output/'analyser_workspace/manifest.csv')
        for row in session.rows:
            result = session.verify(row)
            assert result.can_open and result.wav_hash_matches
            assert result.annotation_modified_since_audit is False
            assert result.original_json.is_relative_to(output)
            if queue == 'negative_final':
                assert row.raw['manual_chirp_id'] == ''
                with pytest.raises(ValueError, match='manuelles'):
                    session.set_quality_status(row, 'verified_correct')
