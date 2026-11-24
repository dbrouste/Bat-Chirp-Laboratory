"""Reject scenario-only uniqueness before REPLACE discards another run."""
import sqlite3
from contextlib import closing

import pytest

from laboratory.local_campaign import connect_db


@pytest.mark.parametrize("unique_index", [False, True])
@pytest.mark.parametrize("column", ["scenario", "SCENARIO"])
@pytest.mark.parametrize("existing_experiments", [False, True])
def test_rejects_scenario_only_unique_without_changing_data(
        tmp_path, unique_index, column, existing_experiments):
    path = tmp_path / "campaign.sqlite"
    experiment = ("first", 5, "completed", "now", None, None, "out", None)
    result = ("first", 1.5, "local", "all", 3, 3, 0, 1, 0)
    with closing(connect_db(path)) as db:
        db.execute("DROP TABLE threshold_results")
        if existing_experiments:
            db.execute("INSERT INTO experiments VALUES (?,?,?,?,?,?,?,?)", experiment)
        else:
            db.execute("DROP TABLE experiments")
        constraint = "" if unique_index else f", UNIQUE ({column})"
        db.execute("""CREATE TABLE threshold_results (
            RUN_ID TEXT NOT NULL, THRESHOLD REAL NOT NULL,
            SCENARIO TEXT NOT NULL, REGION TEXT NOT NULL,
            TOTAL INTEGER NOT NULL, VALID INTEGER NOT NULL,
            BASELINE_FLAGGED INTEGER NOT NULL, NEW_ALERTS INTEGER NOT NULL,
            LEGACY_NEW_ALERTS INTEGER NOT NULL,
            PRIMARY KEY (run_id, threshold, scenario, region)""" + constraint + ")")
        if unique_index:
            db.execute(f'CREATE UNIQUE INDEX "result scenario index" '
                       f'ON threshold_results ({column})')
        db.execute("INSERT INTO threshold_results VALUES (?,?,?,?,?,?,?,?,?)", result)
        db.commit()
        schema = db.execute("SELECT * FROM sqlite_master").fetchall()
    original_bytes = path.read_bytes()

    with pytest.raises(sqlite3.OperationalError,
                       match=r"threshold_results must not have UNIQUE \(scenario\)"):
        with closing(connect_db(path)):
            pass

    assert path.read_bytes() == original_bytes
    with closing(sqlite3.connect(path)) as db:
        assert db.execute("SELECT * FROM threshold_results").fetchall() == [result]
        assert db.execute("SELECT * FROM sqlite_master").fetchall() == schema
        if existing_experiments:
            assert db.execute("SELECT * FROM experiments").fetchall() == [experiment]


@pytest.mark.parametrize("index", [
    None,
    "CREATE INDEX result_scenarios ON threshold_results (scenario)",
    "CREATE UNIQUE INDEX result_key ON threshold_results (run_id, threshold, scenario, region)",
])
def test_compatible_indexes_preserve_results(tmp_path, index):
    path = tmp_path / "campaign.sqlite"
    with closing(connect_db(path)) as db:
        if index:
            db.execute(index)
        db.commit()
    with closing(connect_db(path)) as db:
        for run_id, threshold, region, alerts in (
                ("first", 1.5, "all", 1), ("second", 1.5, "all", 2),
                ("second", 2.0, "all", 4), ("second", 1.5, "start", 5),
                ("second", 1.5, "all", 3)):
            db.execute("INSERT OR REPLACE INTO threshold_results VALUES (?,?,?,?,?,?,?,?,?)",
                       (run_id, threshold, "local", region, 3, 3, 0, alerts, 0))
        db.commit()
        assert db.execute(
            "SELECT run_id, threshold, region, new_alerts FROM threshold_results "
            "ORDER BY run_id, threshold, region"
        ).fetchall() == [
            ("first", 1.5, "all", 1), ("second", 1.5, "all", 3),
            ("second", 1.5, "start", 5), ("second", 2.0, "all", 4),
        ]
