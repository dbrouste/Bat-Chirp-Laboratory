"""Schema initialization must commit both tables or leave the database unchanged."""
import sqlite3
from contextlib import closing

import pytest

from laboratory import local_campaign


@pytest.mark.parametrize("existing_data", [False, True])
def test_second_create_failure_rolls_back_schema(tmp_path, monkeypatch, existing_data):
    path = tmp_path / "campaign.sqlite"
    original_connect = sqlite3.connect
    if existing_data:
        with closing(original_connect(path)) as db:
            db.execute("CREATE TABLE retained (value TEXT)")
            db.execute("INSERT INTO retained VALUES ('keep me')")
            db.commit()

    creates = []

    def authorize(action, name, *unused):
        if action == sqlite3.SQLITE_CREATE_TABLE:
            creates.append(name)
            if name == "threshold_results":
                return sqlite3.SQLITE_DENY
        return sqlite3.SQLITE_OK

    def connect(*args, **kwargs):
        db = original_connect(*args, **kwargs)
        db.set_authorizer(authorize)
        return db

    monkeypatch.setattr(local_campaign.sqlite3, "connect", connect)
    with pytest.raises(sqlite3.DatabaseError, match="not authorized"):
        local_campaign.connect_db(path)

    assert creates == ["experiments", "threshold_results"]
    with closing(original_connect(path)) as db:
        tables = db.execute("SELECT name FROM sqlite_master WHERE type='table'").fetchall()
        assert tables == ([("retained",)] if existing_data else [])
        if existing_data:
            assert db.execute("SELECT value FROM retained").fetchall() == [("keep me",)]


def test_successful_schema_creation_is_committed(tmp_path):
    path = tmp_path / "campaign.sqlite"
    with closing(local_campaign.connect_db(path)) as db:
        assert not db.in_transaction
    with closing(sqlite3.connect(path)) as db:
        assert db.execute("SELECT COUNT(*) FROM experiments").fetchone() == (0,)
        assert db.execute("SELECT COUNT(*) FROM threshold_results").fetchone() == (0,)
