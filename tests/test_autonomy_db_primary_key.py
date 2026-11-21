"""Campaign results need their composite primary key when resuming."""
import sqlite3

import pytest

from laboratory.local_campaign import connect_db


@pytest.mark.parametrize("constraint", [
    "",
    ", UNIQUE (run_id, threshold, scenario, region)",
    ", PRIMARY KEY (run_id)",
    ", PRIMARY KEY (run_id, threshold, scenario)",
    ", PRIMARY KEY (run_id, threshold, scenario, region, total)",
])
def test_rejects_incorrect_primary_key_without_changing_database(tmp_path, constraint):
    path = tmp_path / "campaign.sqlite"
    row = ("existing", 1.5, "local", "all", 3, 3, 0, 1, 0)
    with sqlite3.connect(path) as db:
        db.execute("""CREATE TABLE threshold_results (
            run_id TEXT NOT NULL, threshold REAL NOT NULL,
            scenario TEXT NOT NULL, region TEXT NOT NULL,
            total INTEGER NOT NULL, valid INTEGER NOT NULL,
            baseline_flagged INTEGER NOT NULL, new_alerts INTEGER NOT NULL,
            legacy_new_alerts INTEGER NOT NULL""" + constraint + ")")
        db.execute("INSERT INTO threshold_results VALUES (?,?,?,?,?,?,?,?,?)", row)

    with pytest.raises(sqlite3.OperationalError, match="threshold_results requires PRIMARY KEY"):
        connect_db(path)

    with sqlite3.connect(path) as db:
        assert db.execute("SELECT * FROM threshold_results").fetchall() == [row]
        assert db.execute(
            "SELECT name FROM sqlite_master WHERE type='table'"
        ).fetchall() == [("threshold_results",)]


def test_valid_primary_key_replaces_results_after_reopening(tmp_path):
    path = tmp_path / "campaign.sqlite"
    for new_alerts in (1, 2):
        db = connect_db(path)
        try:
            db.execute("""INSERT OR REPLACE INTO threshold_results
                VALUES (?,?,?,?,?,?,?,?,?)""",
                ("existing", 1.5, "local", "all", 3, 3, 0, new_alerts, 0))
            db.commit()
            assert db.execute(
                "SELECT new_alerts FROM threshold_results"
            ).fetchall() == [(new_alerts,)]
        finally:
            db.close()
