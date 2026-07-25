# SPDX-FileCopyrightText: 2026 Dylan Lee
# SPDX-License-Identifier: Apache-2.0

import hashlib
import os
from pathlib import Path
import time
import zipfile

import pytest

from tools.archive_release import build_archive


def _sha256(path):
    return hashlib.sha256(path.read_bytes()).hexdigest()


def test_release_archive_is_complete_reproducible_and_checksummed(tmp_path):
    source = tmp_path / "Astrolabe.dist"
    (source / "trackball_daemon" / "schemas").mkdir(parents=True)
    (source / "Astrolabe.exe").write_bytes(b"MZ\x00app")
    schema = source / "trackball_daemon" / "schemas" / "contract.json"
    schema.write_text('{"schema": 1}\n', encoding="utf-8")
    first = tmp_path / "first.zip"
    second = tmp_path / "second.zip"

    first_hash = build_archive(source, first)
    time.sleep(0.01)
    os.utime(schema, None)
    second_hash = build_archive(source, second)

    assert first_hash == second_hash == _sha256(first) == _sha256(second)
    assert first.read_bytes() == second.read_bytes()
    assert first.with_name("first.zip.sha256").read_text(encoding="ascii") == (
        f"{first_hash}  first.zip\n"
    )
    with zipfile.ZipFile(first) as archive:
        assert archive.namelist() == [
            "Astrolabe.dist/",
            "Astrolabe.dist/trackball_daemon/",
            "Astrolabe.dist/trackball_daemon/schemas/",
            "Astrolabe.dist/Astrolabe.exe",
            "Astrolabe.dist/trackball_daemon/schemas/contract.json",
        ]
        assert all(info.date_time == (1980, 1, 1, 0, 0, 0) for info in archive.infolist())


def test_release_archive_rejects_empty_or_recursive_sources(tmp_path):
    source = tmp_path / "empty.dist"
    source.mkdir()
    with pytest.raises(ValueError, match="contains no files"):
        build_archive(source, tmp_path / "empty.zip")

    (source / "payload.txt").write_text("payload", encoding="utf-8")
    with pytest.raises(ValueError, match="must not be inside"):
        build_archive(source, source / "recursive.zip")
