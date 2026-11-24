"""Reject incomplete campaign schemas before returning a connection."""
import sqlite3

import pytest

from laboratory.local_campaign import connect_db


REQUIRED_COLUMNS = {
    "experiments": (
        "run_id", "window_points", "state", "created_at", "finished_at",
        "elapsed_seconds", "output_dir", "error",
    ),
    "threshold_results": (
        "run_id", "threshold", "scenario", "region", "total", "valid",
        "baseline_flagged", "new_alerts", "legacy_new_alerts",
    ),
}


@pytest.mark.parametrize("table,missing", [
    (table, column)
    for table, columns in REQUIRED_COLUMNS.items()
    for column in columns
])
def test_connect_db_rejects_each_missing_column(tmp_path, table, missing):
    path = tmp_path / "campaign.sqlite"
    columns = ", ".join(c for c in REQUIRED_COLUMNS[table] if c != missing)
    with sqlite3.connect(path) as db:
        db.execute(f"CREATE TABLE {table} ({columns})")

    with pytest.raises(sqlite3.OperationalError, match=f"no such column: {missing}"):
        connect_db(path)


def test_run_id_only_schema_is_rejected_without_changing_existing_data(tmp_path):
    path = tmp_path / "campaign.sqlite"
    with sqlite3.connect(path) as db:
        db.execute("CREATE TABLE experiments (run_id TEXT PRIMARY KEY)")
        db.execute("INSERT INTO experiments VALUES ('existing')")

    with pytest.raises(sqlite3.OperationalError, match="no such column: window_points"):
        connect_db(path)

    with sqlite3.connect(path) as db:
        assert db.execute("SELECT * FROM experiments").fetchall() == [("existing",)]
        assert db.execute(
            "SELECT name FROM sqlite_master WHERE type='table'"
        ).fetchall() == [("experiments",)]


def test_valid_schema_reopens_with_extra_column_and_preserves_data(tmp_path):
    path = tmp_path / "campaign.sqlite"
    db = connect_db(path)
    try:
        db.execute("ALTER TABLE experiments ADD COLUMN notes TEXT")
        db.execute("""INSERT INTO experiments
            (run_id, window_points, state, created_at, output_dir, notes)
            VALUES ('existing', 5, 'completed', 'now', 'out', 'keep')""")
        db.commit()
    finally:
        db.close()

    db = connect_db(path)
    try:
        assert db.execute("SELECT run_id, notes FROM experiments").fetchall() == [
            ("existing", "keep")
        ]
        assert db.execute("SELECT COUNT(*) FROM threshold_results").fetchone() == (0,)
    finally:
        db.close()
