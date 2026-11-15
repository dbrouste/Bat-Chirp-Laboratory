"""Scientific hypothesis catalog is read-only until its bench is ready."""
from pathlib import Path

import pytest

from laboratory import research_ideas
from laboratory import code_agent


def test_catalog_contains_the_four_scientific_hypotheses():
    doc = research_ideas.read_ideas()
    ideas = {entry["id"]: entry for entry in doc["ideas"]}
    assert set(ideas) == {"H01", "H02", "H03", "H04"}
    assert all(entry["status"] == "needs_benchmark"
               for entry in ideas.values())
    assert all(entry["experiments"] and entry["metrics_needed"]
               for entry in ideas.values())
    assert all(entry["depends_on"] for entry in ideas.values())


@pytest.mark.parametrize("id_", ["H01", "H02", "H03", "H04"])
def test_unmeasurable_ideas_cannot_launch_code_agent(id_):
    with pytest.raises(ValueError, match="benchmark and reference labels"):
        research_ideas.select_idea(id_, for_code_agent=True)
    with pytest.raises(ValueError, match="benchmark and reference labels"):
        code_agent.run(dataset=Path("does-not-exist"),
                       processing_root=Path("does-not-exist"),
                       out=Path("does-not-exist"),
                       parameter_agent_summary=Path("does-not-exist"),
                       research_idea_id=id_)


def test_supported_ready_idea_needs_explicit_file_and_benchmark(tmp_path):
    base = research_ideas.read_ideas()["ideas"][0]
    import copy
    import yaml

    entry = copy.deepcopy(base)
    entry["status"] = "ready_for_code_agent"
    path = tmp_path / "ideas.yaml"
    def write():
        path.write_text(yaml.safe_dump({
            "schema": "chirp_research_ideas_v1",
            "ideas": [entry],
        }, allow_unicode=True), encoding="utf-8")
    write()
    with pytest.raises(ValueError, match="unsupported benchmark"):
        research_ideas.select_idea(entry["id"], path=path,
                                   for_code_agent=True)
    entry["benchmark"] = "detector_baseline_v1"
    write()
    with pytest.raises(ValueError, match="limited to detection.py"):
        research_ideas.select_idea(entry["id"], path=path,
                                   for_code_agent=True)
    entry["allowed_files"] = ["bat_analysis/detection.py"]
    write()
    assert research_ideas.select_idea(entry["id"], path=path,
                                      for_code_agent=True)["id"] == "H01"


def test_duplicate_ideas_are_rejected(tmp_path):
    import yaml
    entry = research_ideas.read_ideas()["ideas"][0]
    path = tmp_path / "dup.yaml"
    path.write_text(yaml.safe_dump({
        "schema": "chirp_research_ideas_v1",
        "ideas": [entry, entry],
    }, allow_unicode=True), encoding="utf-8")
    with pytest.raises(ValueError, match="Duplicate"):
        research_ideas.read_ideas(path)
