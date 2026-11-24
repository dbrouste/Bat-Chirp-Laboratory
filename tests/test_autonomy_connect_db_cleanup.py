"""Regression coverage for connection ownership during schema initialization."""
import sqlite3
from unittest.mock import Mock

import pytest

from laboratory import local_campaign


@pytest.mark.parametrize("failure_stage", ["first_create", "second_create", "commit"])
@pytest.mark.parametrize("close_fails", [False, True])
def test_initialization_failure_closes_connection(monkeypatch, failure_stage, close_fails):
    db = Mock(spec=sqlite3.Connection)
    failure = sqlite3.OperationalError("initialization failed")
    if failure_stage == "first_create":
        db.execute.side_effect = failure
    elif failure_stage == "second_create":
        db.execute.side_effect = [None, failure]
    else:
        db.commit.side_effect = failure
    if close_fails:
        db.close.side_effect = sqlite3.OperationalError("cleanup failed")
    monkeypatch.setattr(local_campaign.sqlite3, "connect", Mock(return_value=db))

    with pytest.raises(sqlite3.OperationalError) as caught:
        local_campaign.connect_db(":memory:")

    assert caught.value is failure
    db.close.assert_called_once_with()


def test_successful_connection_remains_open():
    db = local_campaign.connect_db(":memory:")
    try:
        assert db.execute("SELECT COUNT(*) FROM experiments").fetchone() == (0,)
        assert db.execute("SELECT COUNT(*) FROM threshold_results").fetchone() == (0,)
    finally:
        db.close()
