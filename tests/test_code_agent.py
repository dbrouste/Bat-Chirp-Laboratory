"""Safety tests for the Codex detector edit controller.

Mocks never invoke an external LLM, never run real Git worktrees and never
touch the user's actual WAVs. Real Windows integration is validated separately.
"""
from pathlib import Path

import pytest

from laboratory import code_agent


def test_porcelain_review_only_allows_one_unstaged_detector_file(monkeypatch, tmp_path):
    head = "abcd1234"
    worktree = tmp_path / "code"
    worktree.mkdir()
    answers = {
        ("rev-parse", "HEAD"): head,
        ("status", "--porcelain=v1", "--untracked-files=all"):
            " M bat_analysis/detection.py",
        ("diff", "--check"): "",
    }
    monkeypatch.setattr(
        code_agent, "_git", lambda _path, *args: answers[args])
    assert code_agent._verify_edit(worktree, head) == [
        "bat_analysis/detection.py"]

    answers[("status", "--porcelain=v1", "--untracked-files=all")] = (
        " M bat_analysis/detection.py\n?? test.txt")
    with pytest.raises(ValueError, match="Unexpected Git change"):
        code_agent._verify_edit(worktree, head)

    answers[("status", "--porcelain=v1", "--untracked-files=all")] = (
        " M bat_analysis/reassignment_tracking.py")
    with pytest.raises(ValueError, match="outside allowlist"):
        code_agent._verify_edit(worktree, head)

    answers[("status", "--porcelain=v1", "--untracked-files=all")] = (
        "M  bat_analysis/detection.py")
    with pytest.raises(ValueError, match="Unexpected Git change"):
        code_agent._verify_edit(worktree, head)

    answers[("rev-parse", "HEAD")] = "other"
    with pytest.raises(ValueError, match="Git HEAD"):
        code_agent._verify_edit(worktree, head)


def test_codex_prompt_does_not_disclose_dataset_paths():
    baseline = {
        "matched": 420, "annotated": 493, "high_snr_recall": .938,
        "weighted_full_pipeline": .787, "candidates": 1348}
    prompt = code_agent._prompt(code_agent.GOALS[0], baseline, [])
    assert "bat_analysis/detection.py" in prompt
    assert "test partition" in prompt
    assert "Do not commit" in prompt
    assert "420 matched of 493" in prompt
    assert "C:\\Users\\" not in prompt


def test_codex_pilot_requires_matching_frozen_provenance(tmp_path, monkeypatch):
    dataset = tmp_path / "dataset-v1"
    dataset.mkdir()
    repo = tmp_path / "processing"
    repo.mkdir()
    agent_summary = tmp_path / "param_agent.json"
    agent_summary.write_text(
        '{"schema":"detector_agent_v1",'
        '"recommended":{"eligible":true,"options":{"hop":null}},'
        '"dataset_provenance":{"manifest_sha256":"wrong"},'
        '"algorithm_fingerprint":"abc"}', encoding="utf-8")
    monkeypatch.setattr(code_agent, "collect_frozen_dataset",
                        lambda root, *, partition: (
                            {}, {"manifest_sha256": "right"}))
    with pytest.raises(ValueError, match="Dataset changed"):
        code_agent.run(
            dataset=dataset, processing_root=repo, out=tmp_path / "out",
            parameter_agent_summary=agent_summary)
    assert not (tmp_path / "out").exists()


def test_codex_pilot_rejects_outputs_inside_dataset_or_processing(tmp_path):
    dataset = tmp_path / "data"
    source = tmp_path / "src"
    dataset.mkdir()
    source.mkdir()
    summary = tmp_path / "summary.json"
    summary.write_text(
        '{"schema":"detector_agent_v1",'
        '"recommended":{"eligible":true,"options":{}},'
        '"dataset_provenance":{}}', encoding="utf-8")
    with pytest.raises(ValueError, match="outside corpus"):
        code_agent.run(
            dataset=dataset, processing_root=source,
            out=dataset / "agent", parameter_agent_summary=summary)
    with pytest.raises(ValueError, match="outside corpus"):
        code_agent.run(
            dataset=dataset, processing_root=source,
            out=source / "agent", parameter_agent_summary=summary)
