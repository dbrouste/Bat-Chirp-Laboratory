"""Reject uniqueness that would let REPLACE discard another threshold."""
import sqlite3
from contextlib import closing

import pytest

from laboratory.local_campaign import connect_db


@pytest.mark.parametrize("unique_index", [False, True])
@pytest.mark.parametrize("columns", ["run_id, scenario, region", "REGION, RUN_ID, SCENARIO"])
@pytest.mark.parametrize("existing_experiments", [False, True])
def test_rejects_threshold_loss_unique_without_changing_data(
        tmp_path, unique_index, columns, existing_experiments):
    path = tmp_path / "campaign.sqlite"
    experiment = ("existing", 5, "completed", "now", None, None, "out", None)
    result = ("existing", 1.0, "local", "all", 3, 3, 0, 1, 0)
    with closing(connect_db(path)) as db:
        db.execute("DROP TABLE threshold_results")
        if existing_experiments:
            db.execute("INSERT INTO experiments VALUES (?,?,?,?,?,?,?,?)", experiment)
        else:
            db.execute("DROP TABLE experiments")
        constraint = "" if unique_index else f", UNIQUE ({columns})"
        db.execute("""CREATE TABLE threshold_results (
            run_id TEXT NOT NULL, threshold REAL NOT NULL,
            scenario TEXT NOT NULL, region TEXT NOT NULL,
            total INTEGER NOT NULL, valid INTEGER NOT NULL,
            baseline_flagged INTEGER NOT NULL, new_alerts INTEGER NOT NULL,
            legacy_new_alerts INTEGER NOT NULL,
            PRIMARY KEY (run_id, threshold, scenario, region)""" + constraint + ")")
        if unique_index:
            db.execute(f'CREATE UNIQUE INDEX "result threshold loss index" '
                       f'ON threshold_results ({columns})')
        db.execute("INSERT INTO threshold_results VALUES (?,?,?,?,?,?,?,?,?)", result)
        db.commit()
        schema = db.execute("SELECT * FROM sqlite_master").fetchall()
    original_bytes = path.read_bytes()

    with pytest.raises(sqlite3.OperationalError,
                       match=r"threshold_results.*UNIQUE.*run_id.*scenario.*region"):
        with closing(connect_db(path)):
            pass

    assert path.read_bytes() == original_bytes
    with closing(sqlite3.connect(path)) as db:
        assert db.execute("SELECT * FROM threshold_results").fetchall() == [result]
        assert db.execute("SELECT * FROM sqlite_master").fetchall() == schema
        if existing_experiments:
            assert db.execute("SELECT * FROM experiments").fetchall() == [experiment]


@pytest.mark.parametrize("index", [
    "CREATE INDEX result_thresholds ON threshold_results (run_id, scenario, region)",
    "CREATE UNIQUE INDEX result_key ON threshold_results (run_id, threshold, scenario, region)",
])
def test_compatible_indexes_preserve_other_thresholds(tmp_path, index):
    path = tmp_path / "campaign.sqlite"
    with closing(connect_db(path)) as db:
        db.execute(index)
        db.commit()
    with closing(connect_db(path)) as db:
        for threshold, alerts in ((1.0, 1), (1.5, 2), (1.5, 3)):
            db.execute("INSERT OR REPLACE INTO threshold_results VALUES (?,?,?,?,?,?,?,?,?)",
                       ("existing", threshold, "local", "all", 3, 3, 0, alerts, 0))
        db.commit()
        assert db.execute(
            "SELECT threshold, new_alerts FROM threshold_results ORDER BY threshold"
        ).fetchall() == [(1.0, 1), (1.5, 3)]
