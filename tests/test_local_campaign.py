"""Offline tests for the local SQLite experiment orchestrator."""
import csv
import json
from pathlib import Path

import pytest

from laboratory.local_campaign import (
    parse_ints,parse_floats,scored_rows,persist_thresholds,
    connect_db,run_key,run_campaign,
)


def write_sample(path):
    path.parent.mkdir(parents=True,exist_ok=True)
    rows=[
        dict(scenario="global",region="all",error="",
             baseline_local_score_eq="0.4",
             perturbed_local_score_eq="2.0",
             baseline_priority="no_alert_unverified",
             perturbed_priority="review_suggested"),
        dict(scenario="local",region="middle",error="",
             baseline_local_score_eq="0.4",
             perturbed_local_score_eq="1.4",
             baseline_priority="no_alert_unverified",
             perturbed_priority="no_alert_unverified"),
        dict(scenario="local",region="start",error="",
             baseline_local_score_eq="1.7",
             perturbed_local_score_eq="3.0",
             baseline_priority="no_alert_unverified",
             perturbed_priority="no_alert_unverified"),
        dict(scenario="trim",region="end",error="",
             baseline_local_score_eq="0.2",
             perturbed_local_score_eq="0.3",
             baseline_priority="no_alert_unverified",
             perturbed_priority="no_alert_unverified"),
    ]
    with path.open("w",encoding="utf-8-sig",newline="") as file:
        writer=csv.DictWriter(file,fieldnames=rows[0])
        writer.writeheader()
        writer.writerows(rows)


def test_parameter_validation():
    assert parse_ints("3, 4,5,7")==(3,4,5,7)
    assert parse_floats("0.75,1, 1.5")==(.75,1,1.5)
    with pytest.raises(ValueError):
        parse_ints("2,5")
    with pytest.raises(ValueError):
        parse_ints("3,3")
    with pytest.raises(ValueError):
        parse_floats("-1,1")


def test_global_region_not_duplicated(tmp_path):
    file=tmp_path/"cases.csv"
    write_sample(file)
    result=scored_rows(file,1.5)
    assert result["global","all"]==[1,1,0,1,1]
    assert result["local","all"]==[2,2,1,0,0]
    assert result["local","start"]==[1,1,1,0,0]


def test_threshold_sweep_reuses_scores_without_reanalysis(tmp_path):
    file=tmp_path/"cases.csv"
    write_sample(file)
    db=connect_db(tmp_path/"tests.sqlite")
    db.execute("""INSERT INTO experiments(run_id,window_points,state,created_at,output_dir)
                  VALUES('sample',5,'completed','now','out')""")
    persist_thresholds(db,"sample",file,[1.0,1.5,2.5])
    result=db.execute("""SELECT threshold,new_alerts FROM threshold_results
          WHERE run_id='sample' AND scenario='local' AND region='all'
          ORDER BY threshold""").fetchall()
    assert result==[(1.,1),(1.5,0),(2.5,0)]
    db.close()


def test_campaign_run_and_resume_without_new_acoustics(tmp_path,monkeypatch):
    from laboratory import local_campaign
    dataset=tmp_path/"dataset"
    dataset.mkdir()
    with (dataset/"manifest.csv").open("w",newline="") as file:
        writer=csv.DictWriter(file,fieldnames=["partition","dataset_wav","dataset_json"])
        writer.writeheader()
        writer.writerow({"partition":"test","dataset_wav":"not-used.wav",
                         "dataset_json":"not-used.json"})
    calls=[]
    def fake_run(cmd,**kwargs):
        calls.append(cmd)
        out=Path(cmd[cmd.index("--output")+1])
        write_sample(out/"sensitivity_cases.csv")
        return type("Proc",(),{"returncode":0})()
    monkeypatch.setattr(local_campaign.subprocess,"run",fake_run)
    output=tmp_path/"output"
    kw=dict(max_chirps=1,skip_chirps=0,windows=(3,5),
            thresholds=(1.,1.5,2.5))
    first=run_campaign(dataset,output,**kw)
    assert len(calls)==2
    assert first["completed"]==2
    assert (output/"campaign_report.html").is_file()
    assert len(json.loads((output/"campaign_summary.json").read_text())["results"])==18
    second=run_campaign(dataset,output,**kw)
    assert len(calls)==2
    assert all(r["state"]=="completed_cached" for r in second["runs"])


def test_budget_allows_resume(tmp_path,monkeypatch):
    from laboratory import local_campaign
    dataset=tmp_path/"dataset"
    dataset.mkdir()
    (dataset/"manifest.csv").write_text("partition,dataset_wav,dataset_json\n",
                                         encoding="utf-8")
    calls=[]
    def fake_run(cmd,**kwargs):
        calls.append(cmd)
        write_sample(Path(cmd[cmd.index("--output")+1])/"sensitivity_cases.csv")
        return type("Proc",(),{"returncode":0})()
    monkeypatch.setattr(local_campaign.subprocess,"run",fake_run)
    out=tmp_path/"output"
    settings=dict(max_chirps=1,skip_chirps=0,windows=(3,4,5),
                  thresholds=(1.5,))
    first=run_campaign(dataset,out,max_trials=1,**settings)
    assert first["completed"]==1
    assert len(calls)==1
    second=run_campaign(dataset,out,max_trials=2,**settings)
    assert second["completed"]==3
    assert len(calls)==3
