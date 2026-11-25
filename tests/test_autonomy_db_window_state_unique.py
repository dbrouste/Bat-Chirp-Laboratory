"""Reject window/state uniqueness before REPLACE can discard another run."""
import sqlite3
from contextlib import closing

import pytest

from laboratory.local_campaign import connect_db


@pytest.mark.parametrize("index_kind", ["constraint", "index", "partial"])
@pytest.mark.parametrize("columns", ["window_points, state", "STATE, WINDOW_POINTS"])
@pytest.mark.parametrize("existing_results", [False, True])
def test_rejects_window_state_unique_without_changing_data(
        tmp_path, index_kind, columns, existing_results):
    path = tmp_path / "campaign.sqlite"
    experiment = ("first", 5, "completed", "now", None, None, "out", None)
    result = ("first", 1.5, "local", "all", 3, 3, 0, 1, 0)
    with closing(connect_db(path)) as db:
        db.execute("DROP TABLE experiments")
        if existing_results:
            db.execute("INSERT INTO threshold_results VALUES (?,?,?,?,?,?,?,?,?)", result)
        else:
            db.execute("DROP TABLE threshold_results")
        constraint = f", UNIQUE ({columns})" if index_kind == "constraint" else ""
        db.execute("""CREATE TABLE experiments (
            run_id TEXT PRIMARY KEY, WINDOW_POINTS INTEGER NOT NULL,
            STATE TEXT NOT NULL, created_at TEXT NOT NULL,
            finished_at TEXT, elapsed_seconds REAL,
            output_dir TEXT NOT NULL, error TEXT""" + constraint + ")")
        if index_kind != "constraint":
            predicate = " WHERE state = 'completed'" if index_kind == "partial" else ""
            db.execute(f'CREATE UNIQUE INDEX "window state index" '
                       f'ON experiments ({columns}){predicate}')
        db.execute("INSERT INTO experiments VALUES (?,?,?,?,?,?,?,?)", experiment)
        db.commit()
        schema = db.execute("SELECT * FROM sqlite_master").fetchall()
    original_bytes = path.read_bytes()

    with pytest.raises(sqlite3.OperationalError,
                       match=r"experiments must not have UNIQUE \(window_points, state\)"):
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
    "CREATE INDEX window_states ON experiments (window_points, state)",
    "CREATE UNIQUE INDEX window_states ON experiments (window_points, state, run_id)",
])
def test_compatible_indexes_preserve_runs_with_same_window_and_state(tmp_path, index):
    path = tmp_path / "campaign.sqlite"
    with closing(connect_db(path)) as db:
        if index:
            db.execute(index)
        db.commit()
    with closing(connect_db(path)) as db:
        for run_id, error in (("first", None), ("second", "old"), ("second", "updated")):
            db.execute("""INSERT OR REPLACE INTO experiments
                (run_id, window_points, state, created_at, output_dir, error)
                VALUES (?, 5, 'completed', 'now', 'out', ?)""", (run_id, error))
        db.commit()
        assert db.execute(
            "SELECT run_id, window_points, state, error FROM experiments ORDER BY run_id"
        ).fetchall() == [
            ("first", 5, "completed", None), ("second", 5, "completed", "updated")
        ]
