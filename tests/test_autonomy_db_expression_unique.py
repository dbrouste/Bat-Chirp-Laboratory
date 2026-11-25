"""Reject experiment expression uniqueness without changing stored data."""
import sqlite3
from contextlib import closing

import pytest

from laboratory.local_campaign import connect_db


@pytest.mark.parametrize("index", [
    'CREATE UNIQUE INDEX "window expression" ON experiments (window_points + 0)',
    "CREATE UNIQUE INDEX expressions ON experiments (lower(state))",
    "CREATE UNIQUE INDEX expressions ON experiments (state, window_points + 0)",
    "CREATE UNIQUE INDEX expressions ON experiments (window_points + 0) "
    "WHERE state = 'completed'",
])
@pytest.mark.parametrize("existing_results", [False, True])
def test_rejects_unique_expressions_preserving_database(tmp_path, index, existing_results):
    path = tmp_path / "campaign.sqlite"
    row = ("existing", 5, "completed", "now", None, None, "out", None)
    result = ("existing", 1.5, "local", "all", 3, 3, 0, 1, 0)
    with closing(connect_db(path)) as db:
        db.execute("INSERT INTO experiments VALUES (?,?,?,?,?,?,?,?)", row)
        if existing_results:
            db.execute("INSERT INTO threshold_results VALUES (?,?,?,?,?,?,?,?,?)", result)
        else:
            db.execute("DROP TABLE threshold_results")
        db.execute(index)
        db.commit()
        schema = db.execute("SELECT * FROM sqlite_master").fetchall()
    before = path.read_bytes()

    with pytest.raises(sqlite3.OperationalError, match=r"experiments.*UNIQUE.*expression"):
        with closing(connect_db(path)):
            pass

    assert path.read_bytes() == before
    with closing(sqlite3.connect(path)) as db:
        assert db.execute("SELECT * FROM sqlite_master").fetchall() == schema
        assert db.execute("SELECT * FROM experiments").fetchall() == [row]
        if existing_results:
            assert db.execute("SELECT * FROM threshold_results").fetchall() == [result]


@pytest.mark.parametrize("index", [
    "CREATE INDEX expressions ON experiments (window_points + 0)",
    "CREATE INDEX expressions ON experiments (state, window_points + 0) "
    "WHERE state = 'completed'",
    "CREATE UNIQUE INDEX windows ON experiments (window_points, run_id)",
])
def test_compatible_indexes_preserve_runs_with_same_window(tmp_path, index):
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
    with closing(connect_db(path)) as db:
        assert db.execute("SELECT run_id FROM experiments ORDER BY run_id").fetchall() == [
            ("first",), ("second",)
        ]
