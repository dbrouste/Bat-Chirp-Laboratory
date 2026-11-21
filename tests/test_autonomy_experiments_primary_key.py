"""Experiment run IDs must remain unique when resuming a campaign."""
import sqlite3
from contextlib import closing

import pytest

from laboratory.local_campaign import collect_rows, connect_db


@pytest.mark.parametrize("constraint", [
    "",
    ", UNIQUE (run_id)",
    ", PRIMARY KEY (state)",
    ", PRIMARY KEY (run_id, state)",
    ", PRIMARY KEY (state, run_id)",
])
def test_rejects_incorrect_experiments_key_without_changes(tmp_path, constraint):
    path = tmp_path / "campaign.sqlite"
    row = ("existing", 5, "completed", "now", None, None, "out", None)
    with closing(sqlite3.connect(path)) as db:
        db.execute("""CREATE TABLE experiments (
            run_id TEXT, window_points INTEGER NOT NULL,
            state TEXT NOT NULL, created_at TEXT NOT NULL,
            finished_at TEXT, elapsed_seconds REAL,
            output_dir TEXT NOT NULL, error TEXT""" + constraint + ")")
        db.execute("INSERT INTO experiments VALUES (?,?,?,?,?,?,?,?)", row)
        db.commit()
        schema = db.execute("SELECT * FROM sqlite_master").fetchall()

    with pytest.raises(sqlite3.OperationalError,
                       match=r"experiments requires PRIMARY KEY \(run_id\)"):
        connect_db(path)

    with closing(sqlite3.connect(path)) as db:
        assert db.execute("SELECT * FROM experiments").fetchall() == [row]
        assert db.execute("SELECT * FROM sqlite_master").fetchall() == schema


def test_valid_experiments_key_reopens_and_replaces_without_duplicate_reports(tmp_path):
    path = tmp_path / "campaign.sqlite"
    with closing(connect_db(path)) as db:
        db.execute("""INSERT INTO threshold_results VALUES
            ('existing', 1.5, 'local', 'all', 3, 3, 0, 1, 0)""")
        db.commit()

    for state in ("running", "completed"):
        with closing(connect_db(path)) as db:
            db.execute("""INSERT OR REPLACE INTO experiments
                (run_id, window_points, state, created_at, output_dir)
                VALUES ('existing', 5, ?, 'now', 'out')""", (state,))
            db.commit()

    with closing(connect_db(path)) as db:
        assert db.execute("SELECT run_id, state FROM experiments").fetchall() == [
            ("existing", "completed")
        ]
        rows = collect_rows(db, ["existing"], [1.5])
        assert len(rows) == 1
        assert rows[0]["window"] == 5
