"""Reject result uniqueness that would let REPLACE discard another region."""
import sqlite3
from contextlib import closing

import pytest

from laboratory.local_campaign import connect_db


@pytest.mark.parametrize("unique_index", [False, True])
@pytest.mark.parametrize("columns", ["run_id, threshold, scenario", "SCENARIO, RUN_ID, THRESHOLD"])
@pytest.mark.parametrize("existing_experiments", [False, True])
def test_rejects_region_collapsing_unique_without_changing_data(
        tmp_path, unique_index, columns, existing_experiments):
    path = tmp_path / "campaign.sqlite"
    experiment = ("existing", 5, "completed", "now", None, None, "out", None)
    result = ("existing", 1.5, "local", "all", 3, 3, 0, 1, 0)
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
            db.execute(f'CREATE UNIQUE INDEX "result region index" '
                       f'ON threshold_results ({columns})')
        db.execute("INSERT INTO threshold_results VALUES (?,?,?,?,?,?,?,?,?)", result)
        db.commit()
        schema = db.execute("SELECT * FROM sqlite_master").fetchall()

    with pytest.raises(sqlite3.OperationalError,
                       match=r"threshold_results.*UNIQUE.*run_id.*threshold.*scenario"):
        connect_db(path)

    with closing(sqlite3.connect(path)) as db:
        assert db.execute("SELECT * FROM threshold_results").fetchall() == [result]
        assert db.execute("SELECT * FROM sqlite_master").fetchall() == schema
        if existing_experiments:
            assert db.execute("SELECT * FROM experiments").fetchall() == [experiment]


@pytest.mark.parametrize("index", [
    "CREATE INDEX result_regions ON threshold_results (run_id, threshold, scenario)",
    "CREATE UNIQUE INDEX result_key ON threshold_results (run_id, threshold, scenario, region)",
])
def test_compatible_indexes_preserve_other_regions(tmp_path, index):
    path = tmp_path / "campaign.sqlite"
    with closing(connect_db(path)) as db:
        db.execute(index)
        db.commit()
    with closing(connect_db(path)) as db:
        for region, alerts in (("all", 1), ("start", 2), ("start", 3)):
            db.execute("INSERT OR REPLACE INTO threshold_results VALUES (?,?,?,?,?,?,?,?,?)",
                       ("existing", 1.5, "local", region, 3, 3, 0, alerts, 0))
        db.commit()
        assert db.execute(
            "SELECT region, new_alerts FROM threshold_results ORDER BY region"
        ).fetchall() == [("all", 1), ("start", 3)]
