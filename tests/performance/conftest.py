"""Shared fixtures for performance tests."""

import time

import pytest
from sqlalchemy import create_engine, event

from tests.conftest import in_memory_session

# Correct impl resolves well under a second; a correlated-subquery regression blows up
# to tens of seconds. 2s sits in that gap: aborts a runaway query mid-flight without
# flaking on a loaded runner.
QUERY_BUDGET_SECONDS = 2.0


@pytest.fixture
def time_constrained_sqlite_session():
    """In-memory SQLite session that aborts any single statement over the budget."""
    engine = create_engine("sqlite:///:memory:")

    @event.listens_for(engine, "before_cursor_execute")
    def _arm(conn, *_args):
        deadline = time.perf_counter() + QUERY_BUDGET_SECONDS
        conn.connection.dbapi_connection.set_progress_handler(
            lambda: time.perf_counter() > deadline, 100_000
        )

    with in_memory_session(engine) as sess:
        yield sess
