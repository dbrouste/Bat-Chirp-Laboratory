"""Worker-failure scoring contracts, with all inputs and outputs in memory.

No audio, external processing checkout, subprocess or report file is used.
Only I/O and worker execution are replaced; aggregation and HTML are real.
"""

from concurrent.futures import Future
from html.parser import HTMLParser
from pathlib import Path

import pytest

from laboratory import runner


class _InlinePool:
    """Exercise the real future.result() failure branch without subprocesses."""

    def __init__(self, *, max_workers, mp_context):
        assert max_workers == 2
        assert mp_context.get_start_method() == "spawn"

    def __enter__(self):
        return self

    def __exit__(self, *exc):
        return False

    def submit(self, function, *args):
        future = Future()
        try:
            future.set_result(function(*args))
        except Exception as exc:
            future.set_exception(exc)
        return future


class _VisibleText(HTMLParser):
    """Exclude embedded Plotly JavaScript/CSS from presentation assertions."""

    def __init__(self):
        super().__init__(convert_charrefs=True)
        self.hidden = 0
        self.parts = []

    def handle_starttag(self, tag, attrs):
        if tag in {"script", "style", "head"}:
            self.hidden += 1

    def handle_endtag(self, tag):
        if tag in {"script", "style", "head"}:
            self.hidden -= 1

    def handle_data(self, data):
        if not self.hidden:
            self.parts.append(data)


def _manual(chirp_id):
    return {
        "chirp_id": chirp_id, "source": "manual", "status": "validated",
        "points": [{"t_ms": 1, "f_khz": 80}, {"t_ms": 3, "f_khz": 60}],
    }


@pytest.fixture(params=[
    ("returned_error", False), ("returned_error", True),
    ("future_exception", False), ("future_exception", True),
], ids=["one-worker-error", "all-workers-error",
        "one-future-exception", "all-futures-exception"])
def failed_run(request, monkeypatch):
    failure_mode, all_failed = request.param
    records = {
        "a.wav": {"chirps": [_manual(1)]},
        "b.wav": {"chirps": [_manual(2), _manual(3)]},
    }
    failed_names = set(records) if all_failed else {"b.wav"}
    root = Path(__file__).resolve().parent / "_virtual_worker_run"
    external = root / "processing"
    out = root / "output"
    detection = external / "bat_analysis" / "detection.py"
    evaluator = external / "benchmark" / "reassignment_detector_seeds.py"
    json_outputs = {}
    html_outputs = {}
    calls = []

    real_is_file = Path.is_file
    real_read_text = Path.read_text

    def is_file(path):
        return path in {detection, evaluator} or real_is_file(path)

    def read_text(path, *args, **kwargs):
        if path == evaluator:
            return "def evaluate(*args, geometry_mode=None, **kwargs): pass\n"
        return real_read_text(path, *args, **kwargs)

    def mkdir(path, *args, **kwargs):
        assert path == out, f"Unexpected directory creation: {path}"

    def write_text(path, text, *args, **kwargs):
        assert path.is_relative_to(out) and path.suffix == ".html"
        html_outputs[path] = text
        return len(text)

    def worker(name, record, wav_root, options, processing_root):
        calls.append(name)
        assert record == records[name]
        if name in failed_names:
            if failure_mode == "future_exception":
                raise RuntimeError("synthetic worker failure")
            return {
                "file": name, "elapsed_s": 0.0,
                "per_file": [{"file": name, "error": "synthetic worker failure"}],
                "details": [], "traceback": "synthetic traceback",
            }
        return {
            "file": name, "elapsed_s": 0.0,
            "per_file": [{"file": name, "annotated": 1, "candidates": 1,
                          "matched": 1, "tracking_succeeded": 1}],
            "details": [{
                "file": name, "chirp_id": 1, "snr_max_db": 40.0,
                "detection_importance_weight": 1.0, "detector_matched": True,
                "tracking_success": True, "within_snr_tolerance_fraction": 1.0,
                "mean_full_interval_error_khz": 0.0,
                "mean_geometric_error_khz_eq": 0.0,
                "mean_geometric_coverage_fraction": 1.0,
                "mean_normalized_error": 0.0, "coverage_fraction": 1.0,
                "start_error_ms": 0.0, "end_error_ms": 0.0,
            }],
        }

    monkeypatch.setattr(Path, "is_file", is_file)
    monkeypatch.setattr(Path, "read_text", read_text)
    monkeypatch.setattr(Path, "mkdir", mkdir)
    monkeypatch.setattr(Path, "write_text", write_text)
    monkeypatch.setattr(runner, "collect_annotations", lambda *args: records)
    monkeypatch.setattr(runner, "fingerprint_inputs", lambda *args: {
        name: {"wav_sha256": "synthetic", "annotation_sha256": name}
        for name in records
    })
    monkeypatch.setattr(runner, "code_fingerprint", lambda *args: "synthetic-code")
    monkeypatch.setattr(runner, "git_info", lambda *args: {
        "git_commit": "synthetic-commit", "git_dirty": False,
    })
    monkeypatch.setattr(runner, "_worker", worker)
    monkeypatch.setattr(runner, "ProcessPoolExecutor", _InlinePool)
    monkeypatch.setattr(runner, "_write_json_atomic",
                        lambda path, value: json_outputs.update({path: value}))
    monkeypatch.setattr(runner, "write_manifest", lambda *args: None)
    monkeypatch.setattr(runner.shutil, "copyfile", lambda *args: None)
    monkeypatch.setattr(runner, "_record_experiment", lambda *args: None)
    # runner sets these directly: make pytest restore the original environment.
    for variable in ("OMP_NUM_THREADS", "OPENBLAS_NUM_THREADS", "MKL_NUM_THREADS",
                     "NUMEXPR_NUM_THREADS"):
        monkeypatch.setenv(variable, "1")

    report = runner.run(
        annotations=root / "manual.json", wav_dir=root / "audio", out=out,
        workers=2, processing_root=external, use_cache=False,
    )
    assert sorted(calls) == sorted(records)
    assert len(html_outputs) == 1
    assert any(path.name == "report.json" for path in json_outputs)
    parser = _VisibleText()
    parser.feed(next(iter(html_outputs.values())))
    return report, failed_names, " ".join(parser.parts)


def test_worker_failures_mark_run_incomplete(failed_run):
    report, failed_names, _ = failed_run
    assert report["metadata"]["status"] == "incomplete"
    assert report["totals"]["files"] == 2
    assert report["totals"]["files_missing"] == len(failed_names)
    assert report["totals"]["files_evaluated"] == 2 - len(failed_names)
    assert {row["file"] for row in report["per_file"] if "error" in row} == failed_names


def test_failed_workers_do_not_remove_known_manual_references(failed_run):
    report, _, _ = failed_run
    # One validated reference in a.wav, two in b.wav, known before execution.
    assert report["totals"]["annotated"] == 3


def test_incomplete_run_does_not_publish_global_weighted_scores(failed_run):
    report, _, _ = failed_run
    # The failed references have no measured SNR, hence no known importance
    # weights. A success-only ratio must not masquerade as a global score.
    assert (
        report["totals"]["weighted_detector_recall"],
        report["totals"]["weighted_full_pipeline_within_tolerance_fraction"],
    ) == (None, None)


def test_unweighted_score_keeps_failed_references_in_denominator(failed_run):
    report, failed_names, _ = failed_run
    # One fully followed reference out of THREE known references, rather
    # than one out of the single reference whose worker returned details.
    # With no successful worker at all, the score must remain unavailable.
    expected = None if len(failed_names) == 2 else 1 / 3
    assert report["totals"]["full_pipeline_within_tolerance_fraction"] == expected


def test_html_explicitly_identifies_incomplete_scores(failed_run):
    report, failed_names, visible = failed_run
    assert report["metadata"]["status"] == "incomplete"
    for name in failed_names:
        assert name in visible
    # Listing file errors alone leaves the headline scores ambiguous.
    assert "incomplet" in visible.casefold(), visible
    if len(failed_names) == 1:
        assert "partiel" in visible.casefold() or "partial" in visible.casefold(), visible
