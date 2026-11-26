"""Reject alert-count uniqueness before REPLACE discards another run's result."""
import sqlite3
from contextlib import closing

import pytest

from laboratory.local_campaign import connect_db


@pytest.mark.parametrize("uniqueness", ["inline", "table", "index", "partial"])
@pytest.mark.parametrize("column", ["new_alerts", "NEW_ALERTS"])
@pytest.mark.parametrize("existing_experiments", [False, True])
def test_rejects_unique_new_alerts_without_changing_data(
        tmp_path, uniqueness, column, existing_experiments):
    path = tmp_path / "campaign.sqlite"
    experiment = ("first", 5, "completed", "now", None, None, "out", None)
    result = ("first", 1.5, "local", "all", 3, 3, 0, 1, 0)
    with closing(connect_db(path)) as db:
        db.execute("DROP TABLE threshold_results")
        if existing_experiments:
            db.execute("INSERT INTO experiments VALUES (?,?,?,?,?,?,?,?)", experiment)
        else:
            db.execute("DROP TABLE experiments")
        inline = " UNIQUE" if uniqueness == "inline" else ""
        constraint = f", UNIQUE ({column})" if uniqueness == "table" else ""
        db.execute(f"""CREATE TABLE threshold_results (
            run_id TEXT NOT NULL, threshold REAL NOT NULL,
            scenario TEXT NOT NULL, region TEXT NOT NULL,
            total INTEGER NOT NULL, valid INTEGER NOT NULL,
            baseline_flagged INTEGER NOT NULL, {column} INTEGER NOT NULL{inline},
            legacy_new_alerts INTEGER NOT NULL,
            PRIMARY KEY (run_id, threshold, scenario, region){constraint})""")
        if uniqueness in ("index", "partial"):
            predicate = " WHERE new_alerts > 0" if uniqueness == "partial" else ""
            db.execute(f'CREATE UNIQUE INDEX "result alerts index" '
                       f'ON threshold_results ({column}){predicate}')
        db.execute("INSERT INTO threshold_results VALUES (?,?,?,?,?,?,?,?,?)", result)
        db.commit()
        schema = db.execute("SELECT * FROM sqlite_master").fetchall()
    original_bytes = path.read_bytes()

    with pytest.raises(sqlite3.OperationalError,
                       match=r"threshold_results must not have UNIQUE \(new_alerts\)"):
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
    "CREATE INDEX result_alerts ON threshold_results (new_alerts)",
    "CREATE UNIQUE INDEX result_key ON threshold_results (run_id, threshold, scenario, region)",
])
def test_compatible_indexes_preserve_runs_with_same_alert_count(tmp_path, index):
    path = tmp_path / "campaign.sqlite"
    with closing(connect_db(path)) as db:
        if index:
            db.execute(index)
        db.commit()
    with closing(connect_db(path)) as db:
        for run_id, legacy in (("first", 0), ("second", 0), ("second", 1)):
            db.execute("INSERT OR REPLACE INTO threshold_results VALUES (?,?,?,?,?,?,?,?,?)",
                       (run_id, 1.5, "local", "all", 3, 3, 0, 1, legacy))
        db.commit()
        assert db.execute(
            "SELECT run_id, new_alerts, legacy_new_alerts "
            "FROM threshold_results ORDER BY run_id"
        ).fetchall() == [("first", 1, 0), ("second", 1, 1)]
