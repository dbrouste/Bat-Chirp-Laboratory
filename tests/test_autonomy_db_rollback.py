"""Failed schema initialization rolls back even when closing fails."""
import sqlite3
from unittest.mock import Mock, call

import pytest

from laboratory import local_campaign


def test_schema_failure_rolls_back_before_failed_close(monkeypatch):
    failure = sqlite3.OperationalError("schema creation failed")

    class FailingConnection(sqlite3.Connection):
        def execute(self, sql, *args, **kwargs):
            if "CREATE TABLE IF NOT EXISTS threshold_results" in sql:
                raise failure
            return super().execute(sql, *args, **kwargs)

        def close(self):
            raise sqlite3.OperationalError("close failed")

    db = sqlite3.connect(":memory:", factory=FailingConnection)
    monkeypatch.setattr(local_campaign.sqlite3, "connect", Mock(return_value=db))
    try:
        with pytest.raises(sqlite3.OperationalError) as caught:
            local_campaign.connect_db(":memory:")

        assert caught.value is failure
        assert not db.in_transaction
        assert db.execute("SELECT name FROM sqlite_master WHERE type='table'").fetchall() == []
    finally:
        sqlite3.Connection.close(db)


@pytest.mark.parametrize("failure_stage", ["begin", "schema", "commit"])
@pytest.mark.parametrize("rollback_fails", [False, True])
@pytest.mark.parametrize("close_fails", [False, True])
def test_cleanup_preserves_initialization_error(
    monkeypatch, failure_stage, rollback_fails, close_fails
):
    db = Mock(spec=sqlite3.Connection)
    failure = sqlite3.OperationalError("initialization failed")
    if failure_stage == "begin":
        db.execute.side_effect = failure
    elif failure_stage == "schema":
        db.execute.side_effect = [None, None, failure]
    else:
        db.commit.side_effect = failure
    if rollback_fails:
        db.rollback.side_effect = sqlite3.OperationalError("rollback failed")
    if close_fails:
        db.close.side_effect = sqlite3.OperationalError("close failed")
    monkeypatch.setattr(local_campaign.sqlite3, "connect", Mock(return_value=db))

    with pytest.raises(sqlite3.OperationalError) as caught:
        local_campaign.connect_db(":memory:")

    assert caught.value is failure
    assert db.mock_calls[-2:] == [call.rollback(), call.close()]
