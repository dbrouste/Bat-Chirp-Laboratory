"""Reject result expression uniqueness without changing stored data."""
import sqlite3
from contextlib import closing

import pytest

from laboratory.local_campaign import connect_db


@pytest.mark.parametrize("index", [
    'CREATE UNIQUE INDEX "result expression" ON threshold_results (lower(scenario))',
    "CREATE UNIQUE INDEX expressions ON threshold_results (threshold + 0)",
    "CREATE UNIQUE INDEX expressions ON threshold_results (region, lower(scenario))",
    "CREATE UNIQUE INDEX expressions ON threshold_results (lower(scenario)) "
    "WHERE region = 'all'",
])
@pytest.mark.parametrize("existing_experiments", [False, True])
def test_rejects_unique_expressions_preserving_database(
        tmp_path, index, existing_experiments):
    path = tmp_path / "campaign.sqlite"
    experiment = ("existing", 5, "completed", "now", None, None, "out", None)
    result = ("existing", 1.5, "local", "all", 3, 3, 0, 1, 0)
    with closing(connect_db(path)) as db:
        if existing_experiments:
            db.execute("INSERT INTO experiments VALUES (?,?,?,?,?,?,?,?)", experiment)
        else:
            db.execute("DROP TABLE experiments")
        db.execute("INSERT INTO threshold_results VALUES (?,?,?,?,?,?,?,?,?)", result)
        db.execute(index)
        db.commit()
        schema = db.execute("SELECT * FROM sqlite_master").fetchall()
    before = path.read_bytes()

    with pytest.raises(sqlite3.OperationalError,
                       match=r"threshold_results.*UNIQUE.*expression"):
        with closing(connect_db(path)):
            pass

    assert path.read_bytes() == before
    with closing(sqlite3.connect(path)) as db:
        assert db.execute("SELECT * FROM sqlite_master").fetchall() == schema
        assert db.execute("SELECT * FROM threshold_results").fetchall() == [result]
        if existing_experiments:
            assert db.execute("SELECT * FROM experiments").fetchall() == [experiment]


@pytest.mark.parametrize("index", [
    "CREATE INDEX expressions ON threshold_results (lower(scenario))",
    "CREATE INDEX expressions ON threshold_results (region, lower(scenario)) "
    "WHERE region = 'all'",
    "CREATE UNIQUE INDEX result_key ON threshold_results "
    "(run_id, threshold, scenario, region)",
])
def test_compatible_indexes_preserve_results_from_other_runs(tmp_path, index):
    path = tmp_path / "campaign.sqlite"
    with closing(connect_db(path)) as db:
        db.execute(index)
        db.commit()
    with closing(connect_db(path)) as db:
        for run_id, alerts in (("first", 1), ("second", 2), ("second", 3)):
            db.execute("INSERT OR REPLACE INTO threshold_results VALUES (?,?,?,?,?,?,?,?,?)",
                       (run_id, 1.5, "local", "all", 3, 3, 0, alerts, 0))
        db.commit()
    with closing(connect_db(path)) as db:
        assert db.execute(
            "SELECT run_id, new_alerts FROM threshold_results ORDER BY run_id"
        ).fetchall() == [("first", 1), ("second", 3)]
