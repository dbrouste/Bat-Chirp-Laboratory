"""Reject finish-time uniqueness before REPLACE can discard another run."""
import sqlite3
from contextlib import closing

import pytest

from laboratory.local_campaign import connect_db


@pytest.mark.parametrize("constraint,index", [
    (" UNIQUE", None),
    ("", "UNIQUE ({column})"),
    ("", 'CREATE UNIQUE INDEX "finish index" ON experiments ({column})'),
    ("", 'CREATE UNIQUE INDEX "finish index" ON experiments ({column}) '
     "WHERE {column} IS NOT NULL"),
])
@pytest.mark.parametrize("column", ["finished_at", "FINISHED_AT"])
@pytest.mark.parametrize("existing_results", [False, True])
def test_rejects_unique_finished_at_without_changing_data(
        tmp_path, constraint, index, column, existing_results):
    path = tmp_path / "campaign.sqlite"
    experiment = ("first", 5, "completed", "start", "finish", None, "out", None)
    result = ("first", 1.5, "local", "all", 3, 3, 0, 1, 0)
    with closing(connect_db(path)) as db:
        db.execute("DROP TABLE experiments")
        if existing_results:
            db.execute("INSERT INTO threshold_results VALUES (?,?,?,?,?,?,?,?,?)", result)
        else:
            db.execute("DROP TABLE threshold_results")
        table_constraint = ""
        if index and index.startswith("UNIQUE"):
            table_constraint = f", {index.format(column=column)}"
        db.execute(f"""CREATE TABLE experiments (
            run_id TEXT PRIMARY KEY, window_points INTEGER NOT NULL,
            state TEXT NOT NULL, created_at TEXT NOT NULL,
            {column} TEXT{constraint}, elapsed_seconds REAL,
            output_dir TEXT NOT NULL, error TEXT{table_constraint})""")
        if index and index.startswith("CREATE"):
            db.execute(index.format(column=column))
        db.execute("INSERT INTO experiments VALUES (?,?,?,?,?,?,?,?)", experiment)
        db.commit()
        schema = db.execute("SELECT * FROM sqlite_master").fetchall()
    original_bytes = path.read_bytes()

    with pytest.raises(sqlite3.OperationalError,
                       match=r"experiments must not have UNIQUE \(finished_at\)"):
        with closing(connect_db(path)):
            pass

    assert path.read_bytes() == original_bytes
    with closing(sqlite3.connect(path)) as db:
        assert db.execute("SELECT * FROM experiments").fetchall() == [experiment]
        assert db.execute("SELECT * FROM sqlite_master").fetchall() == schema
        if existing_results:
            assert db.execute("SELECT * FROM threshold_results").fetchall() == [result]


@pytest.mark.parametrize("index", [
    None,
    "CREATE INDEX finishes ON experiments (finished_at)",
    "CREATE UNIQUE INDEX finishes ON experiments (finished_at, run_id)",
])
@pytest.mark.parametrize("finished_at", [None, "finish"])
def test_compatible_indexes_preserve_runs_with_same_finish_time(
        tmp_path, index, finished_at):
    path = tmp_path / "campaign.sqlite"
    with closing(connect_db(path)) as db:
        if index:
            db.execute(index)
        db.commit()
    with closing(connect_db(path)) as db:
        for run_id, window in (("first", 5), ("second", 7), ("second", 9)):
            db.execute("""INSERT OR REPLACE INTO experiments
                (run_id, window_points, state, created_at, finished_at, output_dir)
                VALUES (?, ?, 'completed', 'start', ?, 'out')""",
                       (run_id, window, finished_at))
        db.commit()
        assert db.execute(
            "SELECT run_id, window_points, finished_at FROM experiments ORDER BY run_id"
        ).fetchall() == [("first", 5, finished_at), ("second", 9, finished_at)]
