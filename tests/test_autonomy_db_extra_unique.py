"""Reject window uniqueness before REPLACE can discard another run."""
import sqlite3
from contextlib import closing

import pytest

from laboratory.local_campaign import connect_db


@pytest.mark.parametrize("unique_index", [False, True])
@pytest.mark.parametrize("existing_results", [False, True])
def test_rejects_unique_window_without_changing_data(
        tmp_path, unique_index, existing_results):
    path = tmp_path / "campaign.sqlite"
    row = ("existing", 5, "completed", "now", None, None, "out", None)
    result = ("existing", 1.5, "local", "all", 3, 3, 0, 1, 0)
    if existing_results:
        with closing(connect_db(path)) as db:
            db.execute("INSERT INTO threshold_results VALUES (?,?,?,?,?,?,?,?,?)", result)
            db.execute("DROP TABLE experiments")
            db.commit()
    with closing(sqlite3.connect(path)) as db:
        constraint = "" if unique_index else ", UNIQUE (window_points)"
        db.execute("""CREATE TABLE experiments (
            run_id TEXT PRIMARY KEY, window_points INTEGER NOT NULL,
            state TEXT NOT NULL, created_at TEXT NOT NULL,
            finished_at TEXT, elapsed_seconds REAL,
            output_dir TEXT NOT NULL, error TEXT""" + constraint + ")")
        if unique_index:
            db.execute('CREATE UNIQUE INDEX "window index" ON experiments (WINDOW_POINTS)')
        db.execute("INSERT INTO experiments VALUES (?,?,?,?,?,?,?,?)", row)
        db.commit()
        schema = db.execute("SELECT * FROM sqlite_master").fetchall()

    with pytest.raises(sqlite3.OperationalError, match=r"experiments.*UNIQUE.*window_points"):
        connect_db(path)

    with closing(sqlite3.connect(path)) as db:
        assert db.execute("SELECT * FROM experiments").fetchall() == [row]
        assert db.execute("SELECT * FROM sqlite_master").fetchall() == schema
        if existing_results:
            assert db.execute("SELECT * FROM threshold_results").fetchall() == [result]


@pytest.mark.parametrize("index", [
    "CREATE INDEX windows ON experiments (window_points)",
    "CREATE UNIQUE INDEX windows ON experiments (window_points, run_id)",
])
def test_compatible_window_indexes_allow_multiple_runs(tmp_path, index):
    path = tmp_path / "campaign.sqlite"
    with closing(connect_db(path)) as db:
        db.execute(index)
        db.commit()
    with closing(connect_db(path)) as db:
        for run_id in ("first", "second"):
            db.execute("""INSERT OR REPLACE INTO experiments
                (run_id, window_points, state, created_at, output_dir)
                VALUES (?, 5, 'completed', 'now', 'out')""", (run_id,))
        db.commit()
        assert db.execute("SELECT run_id FROM experiments ORDER BY run_id").fetchall() == [
            ("first",), ("second",)
        ]
