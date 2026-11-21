"""Primary-key validation follows SQLite's case-insensitive identifiers."""
import sqlite3

import pytest

from laboratory import local_campaign


COLUMNS = {
    "experiments": (
        "run_id", "window_points", "state", "created_at", "finished_at",
        "elapsed_seconds", "output_dir", "error",
    ),
    "threshold_results": (
        "run_id", "threshold", "scenario", "region", "total", "valid",
        "baseline_flagged", "new_alerts", "legacy_new_alerts",
    ),
}
KEYS = {
    "experiments": "run_id",
    "threshold_results": "run_id,threshold,scenario,region",
}


@pytest.fixture
def existing_db(monkeypatch):
    db = sqlite3.connect(":memory:")
    monkeypatch.setattr(local_campaign.sqlite3, "connect", lambda path: db)
    yield db
    db.close()


@pytest.mark.parametrize("table", COLUMNS)
@pytest.mark.parametrize("case", [str.upper, str.title], ids=["upper", "mixed"])
def test_accepts_case_insensitive_primary_key_names(existing_db, table, case):
    columns = ", ".join(case(column) for column in COLUMNS[table])
    existing_db.execute(
        f"CREATE TABLE {table} ({columns}, PRIMARY KEY ({case(KEYS[table])}))"
    )

    assert local_campaign.connect_db(":memory:") is existing_db
    assert not existing_db.in_transaction
    assert existing_db.execute(f"SELECT * FROM {table}").fetchall() == []


@pytest.mark.parametrize("table,key", [
    ("experiments", ""),
    ("experiments", "STATE"),
    ("experiments", "RUN_ID,STATE"),
    ("threshold_results", ""),
    ("threshold_results", "RUN_ID"),
    ("threshold_results", "RUN_ID,THRESHOLD,SCENARIO,TOTAL"),
    ("threshold_results", "RUN_ID,THRESHOLD,SCENARIO,REGION,TOTAL"),
    ("threshold_results", "THRESHOLD,RUN_ID,SCENARIO,REGION"),
])
def test_rejects_wrong_primary_keys_with_uppercase_names(existing_db, table, key):
    columns = ", ".join(column.upper() for column in COLUMNS[table])
    constraint = f", PRIMARY KEY ({key})" if key else ""
    existing_db.execute(f"CREATE TABLE {table} ({columns}{constraint})")

    with pytest.raises(sqlite3.OperationalError, match=f"{table} requires PRIMARY KEY"):
        local_campaign.connect_db(":memory:")
