"""Shared pytest fixtures for daemon-wide process state."""

import logging

import pytest


@pytest.fixture
def daemon_caplog(caplog):
    """Capture daemon child logs even after production logging is configured."""
    daemon_logger = logging.getLogger("trackball_daemon")
    original_propagate = daemon_logger.propagate
    daemon_logger.propagate = True
    try:
        yield caplog
    finally:
        daemon_logger.propagate = original_propagate
