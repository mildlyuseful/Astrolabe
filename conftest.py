# SPDX-FileCopyrightText: 2026 Dylan Lee
# SPDX-License-Identifier: Apache-2.0

"""Pytest bootstrap for the trackball_daemon tests.

Having this file at the repo root puts the project root on sys.path (so `import
trackball_daemon` works) and isolates the per-user config dir + log file into temp
directories so tests never read or write the real per-user configuration root.
"""
import os

import pytest


@pytest.fixture(autouse=True, scope="session")
def _isolate_user_dir(tmp_path_factory):
    """Point the per-user config dir (APPDATA / XDG_CONFIG_HOME) at a session temp dir, so
    Config() and the file logger never touch the real user directory."""
    import logging

    d = tmp_path_factory.mktemp("userdir")
    saved = {k: os.environ.get(k) for k in ("APPDATA", "XDG_CONFIG_HOME")}
    os.environ["APPDATA"] = str(d)
    os.environ["XDG_CONFIG_HOME"] = str(d)
    yield d
    # Release the rotating-log file handle so the temp dir can be cleaned on Windows.
    lg = logging.getLogger("trackball_daemon")
    for h in list(lg.handlers):
        try:
            h.close()
        except Exception:
            pass
        lg.removeHandler(h)
    for k, v in saved.items():
        if v is None:
            os.environ.pop(k, None)
        else:
            os.environ[k] = v


@pytest.fixture
def isolated_config(tmp_path, monkeypatch):
    """Give a single test its own pristine config dir (fresh config.json each time), so
    config-mutating tests don't contaminate one another."""
    monkeypatch.setenv("APPDATA", str(tmp_path))
    monkeypatch.setenv("XDG_CONFIG_HOME", str(tmp_path))
    return tmp_path


@pytest.fixture
def config_dir(isolated_config):
    """The directory the daemon will actually read and write inside an isolated config root.

    Tests that seed a config file want it where the code looks, not where an earlier build looked;
    going through the path authority also keeps them out of the legacy-directory migration, which
    has its own tests.
    """
    from trackball_daemon.paths import canonical_config_dir

    directory = canonical_config_dir()
    directory.mkdir(parents=True, exist_ok=True)
    return directory
